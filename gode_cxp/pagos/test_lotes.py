"""Lotes de pago: qué se puede pagar, cómo se agrupa, el archivo TEF, la transmisión y el reintento."""
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import cuenta_verificada, factura_aprobada, usuario, xml_con
from gode_cxp.pagos.lotes import crear_lotes, facturas_pagables, generar_archivo, marcar_transmitido, nuevo_lote_pendientes
from gode_cxp.pagos.tef import leer_tef
from gode_cxp.setup.produccion import configurar_pagos

CLABE_12 = "072180007090045065"
# La misma CLABE Banamex válida que en test_cuentas_bancarias (la del plan, ...901, trae el dígito
# verificador mal y validar_clabe la rechaza).
CLABE_06 = "002180012345678906"
FECHA = date(2026, 9, 17)
TESORERIA = "prueba.tesoreria@cxp.local"
# Una empresa que no existe: sirve para simular datos de otra empresa del sitio sin dar de alta un
# catálogo de cuentas entero (frappe.db.set_value no valida los Link).
OTRA_EMPRESA = "EMPRESA AJENA"


class TestLotes(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()
        banco = frappe.db.get_value("Account", {"company": pruebas_comun.EMPRESA, "account_type": "Bank", "is_group": 0}, "name")
        configurar_pagos(pruebas_comun.EMPRESA, "000181511777", "7007", "8382129",
                         "GASTRONOMICA DE ESPECIALIDADES GODE", "pago gode", banco, dry_run=False)

    def setUp(self):
        frappe.set_user("Administrator")
        pruebas_comun.limpiar()
        # Proveedor A (interbancario) con dos facturas; proveedor B (Banamex) con una.
        self.fa1 = factura_aprobada(ejemplos.INGRESO_40)                                            # 1160.00
        self.fa2 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "11111111-2222-3333-4444-555555555555", "77"))
        self.fb = factura_aprobada(ejemplos.INGRESO_33_RETENCIONES)
        self.cta_a = cuenta_verificada(self.fa1.supplier, CLABE_12)
        self.cta_b = cuenta_verificada(self.fb.supplier, CLABE_06, banco="Banamex", sucursal="7005", cuenta="7479513")

    def tearDown(self):
        frappe.set_user("Administrator")

    def _lote_a_mano(self, filas, importe=None, **extras):
        """Lote capturado 'a mano' (como desde el formulario), para probar lo que crear_lotes no
        puede producir: filas repetidas de la misma factura, o un lote nuevo que ya trae secuencial.

        `filas` = [(factura, importe)]; todas cuelgan de una sola transferencia del proveedor A."""
        lote = frappe.new_doc("Lote de Pago")
        lote.update({"company": pruebas_comun.EMPRESA, "fecha_pago": FECHA, "naturaleza": "12",
                     "concepto": "pago gode", "referencia_numerica": "0170926",
                     "cuenta_bancaria_empresa": frappe.db.get_single_value("Configuracion CxP", "cuenta_bancaria_empresa")})
        lote.append("transferencias", {
            "proveedor": self.fa1.supplier, "cuenta_bancaria": self.cta_a, "cuenta_tef": CLABE_12,
            "beneficiario_tef": frappe.db.get_value("Bank Account", self.cta_a, "nombre_tef"),
            "importe": importe if importe is not None else sum(i for _f, i in filas)})
        for factura, imp in filas:
            lote.append("facturas", {"transferencia": 1, "proveedor": self.fa1.supplier,
                                     "factura": factura, "importe": imp})
        lote.update(extras)
        return lote

    def _lote_transmitido(self, importe=100):
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": importe}])
        frappe.get_doc("Lote de Pago", nombre).submit()
        generar_archivo(nombre)
        marcar_transmitido(nombre, "119938")
        return nombre

    def test_facturas_pagables(self):
        nombres = {f["name"] for f in facturas_pagables(pruebas_comun.EMPRESA)}
        self.assertEqual(nombres, {self.fa1.name, self.fa2.name, self.fb.name})

    def test_crea_un_lote_por_naturaleza_y_una_transferencia_por_proveedor(self):
        lotes = crear_lotes(pruebas_comun.EMPRESA, FECHA, [
            {"factura": self.fa1.name, "importe": self.fa1.outstanding_amount},
            {"factura": self.fa2.name, "importe": 500},
            {"factura": self.fb.name, "importe": self.fb.outstanding_amount},
        ])
        self.assertEqual(len(lotes), 2)
        por_nat = {frappe.db.get_value("Lote de Pago", n, "naturaleza"): frappe.get_doc("Lote de Pago", n) for n in lotes}
        l12, l06 = por_nat["12"], por_nat["06"]
        self.assertEqual(len(l12.transferencias), 1)
        self.assertEqual(l12.transferencias[0].importe, self.fa1.outstanding_amount + 500)
        self.assertEqual(l12.transferencias[0].beneficiario_tef, "AVICOLA,DEL CARMEN SA DE CV/")
        self.assertEqual(l12.transferencias[0].cuenta_tef, CLABE_12)
        self.assertEqual({f.factura for f in l12.facturas}, {self.fa1.name, self.fa2.name})
        self.assertEqual(l12.total_lote, self.fa1.outstanding_amount + 500)
        self.assertEqual((l12.estado_lote, l12.docstatus, l12.concepto, l12.referencia_numerica), ("Preparado", 0, "pago gode", "0170926"))
        self.assertEqual(l06.transferencias[0].cuenta_tef, "70057479513")
        # Las facturas siguen libres hasta autorizar…
        self.assertFalse(frappe.db.get_value("Purchase Invoice", self.fa1.name, "en_lote"))
        # …pero ya no salen como pagables mientras el lote esté vivo (Preparado cuenta como activo)
        self.assertEqual(facturas_pagables(pruebas_comun.EMPRESA), [])

    def test_validaciones(self):
        with self.assertRaises(frappe.ValidationError):   # importe mayor al saldo
            crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": self.fa1.outstanding_amount + 1}])
        with self.assertRaises(frappe.ValidationError):   # importe cero
            crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 0}])
        frappe.db.set_value("Bank Account", self.cta_a, "verificada", 0)
        with self.assertRaises(frappe.ValidationError):   # cuenta no verificada
            crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        frappe.db.set_value("Bank Account", self.cta_a, "verificada", 1)
        frappe.db.set_value("Supplier", self.fa1.supplier, "bloqueado_pagos", 1)
        with self.assertRaises(frappe.ValidationError):   # proveedor bloqueado
            crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        frappe.db.set_value("Supplier", self.fa1.supplier, "bloqueado_pagos", 0)
        borrador = frappe.copy_doc(self.fa1)
        borrador.cfdi_uuid = None; borrador.cfdi_recibido = None; borrador.estado_revision = None
        # copy_doc NO limpia el docstatus cuando corre dentro de las pruebas (frappe.copy_doc:
        # `if not local.flags.in_test`), así que sin esto la copia se insertaría como enviada.
        borrador.docstatus = 0
        borrador.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):   # factura no aprobada
            crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": borrador.name, "importe": 100}])

    def test_crear_lotes_valida_la_forma_de_las_partidas(self):
        """`partidas` llega del diálogo del escritorio como texto JSON y por la API como lo que sea.
        Una forma inesperada tiene que salir como un mensaje en español, no como un KeyError o un
        TypeError (que en una petición web es un error 500 sin explicación)."""
        for malas in ("no es json",                                   # texto que no es JSON
                      '{"factura": "X"}',                             # JSON pero un dict, no una lista
                      "[]",                                           # lista vacía
                      ["no es un dict"],
                      [{"factura": self.fa1.name}],                   # sin importe
                      [{"importe": 100}],                             # sin factura
                      [{"factura": self.fa1.name, "importe": "mucho"}],
                      [{"factura": self.fa1.name, "importe": None}]):
            with self.assertRaises(frappe.ValidationError):
                crear_lotes(pruebas_comun.EMPRESA, FECHA, malas)

    def test_la_misma_factura_dos_veces_no_se_paga_doble(self):
        """El candado más importante: dos partidas (o dos filas) de la misma factura sumaban sin que
        nadie comparara el total contra el saldo, así que el proveedor cobraba dos veces."""
        with self.assertRaises(frappe.ValidationError):   # desde crear_lotes
            crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 600},
                                                       {"factura": self.fa1.name, "importe": 600}])
        with self.assertRaises(frappe.ValidationError):   # y en un lote armado a mano
            self._lote_a_mano([(self.fa1.name, 600), (self.fa1.name, 600)]).insert()

    def test_el_secuencial_no_se_reutiliza_aunque_se_cancele_el_lote(self):
        """Un secuencial que ya se usó pudo irse al banco: reutilizarlo mandaría dos archivos con el
        mismo número de lote y BancaNet no distingue cuál es cuál."""
        (n1,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        l1 = frappe.get_doc("Lote de Pago", n1); l1.submit(); generar_archivo(n1)
        self.assertEqual(frappe.db.get_value("Lote de Pago", n1, "secuencial"), 1)
        l1.reload(); l1.cancel()
        (n2,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa2.name, "importe": 100}])
        frappe.get_doc("Lote de Pago", n2).submit(); generar_archivo(n2)
        self.assertEqual(frappe.db.get_value("Lote de Pago", n2, "secuencial"), 2)

    def test_el_banco_solo_admite_99_lotes_por_dia(self):
        (n1,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        frappe.get_doc("Lote de Pago", n1).submit(); generar_archivo(n1)
        frappe.db.set_value("Lote de Pago", n1, "secuencial", 99, update_modified=False)
        (n2,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa2.name, "importe": 100}])
        frappe.get_doc("Lote de Pago", n2).submit()
        with self.assertRaises(frappe.ValidationError):
            generar_archivo(n2)

    def test_un_lote_nuevo_nace_sin_secuencial_ni_archivo(self):
        """Nadie puede estrenar un lote con el secuencial, el archivo o el estado de otro: son los
        campos que dicen qué se mandó al banco."""
        lote = self._lote_a_mano(
            [(self.fa1.name, 100)], secuencial=7, nombre_archivo="170926-0007-12.txt",
            archivo_tef="/private/files/170926-0007-12.txt", generado_el=now_datetime(),
            transmitido_el=now_datetime(), autorizacion_banco="999999", estado_lote="Transmitido")
        lote.transferencias[0].update({"estado_pago": "Aplicado", "linea_tef": 3, "clave_rastreo": "ABC123",
                                       "motivo_rechazo": "cuenta inexistente"})
        lote.insert()
        lote.reload()
        self.assertEqual(lote.estado_lote, "Preparado")
        for campo in ("secuencial", "nombre_archivo", "archivo_tef", "generado_el", "transmitido_el",
                      "autorizacion_banco"):
            self.assertFalse(lote.get(campo), f"{campo} debería nacer vacío")
        t = lote.transferencias[0]
        self.assertEqual(t.estado_pago, "Pendiente")
        for campo in ("linea_tef", "clave_rastreo", "motivo_rechazo", "pago", "reintentado_en"):
            self.assertFalse(t.get(campo), f"transferencia.{campo} debería nacer vacío")

    def test_la_enmienda_de_un_lote_cancelado_nace_limpia(self):
        """El botón 'Amend' del escritorio copia hasta los campos no_copy, así que la enmienda
        llegaría con el secuencial y el archivo del lote que ya se mandó al banco. frappe.copy_doc
        con sus valores por omisión hace exactamente lo mismo."""
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        lote = frappe.get_doc("Lote de Pago", nombre); lote.submit(); generar_archivo(nombre)
        lote.reload(); lote.cancel(); lote.reload()
        self.assertEqual((lote.estado_lote, lote.secuencial), ("Cancelado", 1))
        enmienda = frappe.copy_doc(lote)
        enmienda.amended_from = lote.name
        enmienda.docstatus = 0
        enmienda.insert()
        self.assertEqual(enmienda.estado_lote, "Preparado")
        self.assertFalse(enmienda.secuencial)
        self.assertFalse(enmienda.nombre_archivo)
        self.assertFalse(enmienda.archivo_tef)
        self.assertFalse(enmienda.generado_el)
        self.assertEqual(enmienda.transferencias[0].estado_pago, "Pendiente")
        self.assertFalse(enmienda.transferencias[0].linea_tef)

    def test_factura_en_dos_lotes_bloqueada(self):
        crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        with self.assertRaises(frappe.ValidationError):
            crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])

    def test_autorizar_generar_transmitir(self):
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 1000}, {"factura": self.fa2.name, "importe": 160}])
        lote = frappe.get_doc("Lote de Pago", nombre)
        with self.assertRaises(frappe.ValidationError):   # no se genera sin autorizar
            generar_archivo(nombre)
        lote.submit()
        self.assertEqual(frappe.db.get_value("Lote de Pago", nombre, "estado_lote"), "Autorizado")
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa1.name, "en_lote"), nombre)
        r = generar_archivo(nombre)
        lote.reload()
        self.assertEqual((lote.estado_lote, lote.secuencial, lote.nombre_archivo), ("Exportado", 1, "170926-0001-12.txt"))
        self.assertEqual(lote.transferencias[0].linea_tef, 1)
        contenido = frappe.get_doc("File", {"file_url": r["file_url"]}).get_content()
        leido = leer_tef(contenido if isinstance(contenido, bytes) else contenido.encode("ascii"))
        self.assertEqual((leido["naturaleza"], leido["secuencial"], float(leido["total"])), ("12", 1, 1160.0))
        self.assertEqual(leido["transferencias"][0]["beneficiario"], "AVICOLA,DEL CARMEN SA DE CV/")
        # Segundo lote del mismo día toma el secuencial 2 aunque sea de otra naturaleza
        (n2,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fb.name, "importe": 100}])
        l2 = frappe.get_doc("Lote de Pago", n2); l2.submit(); generar_archivo(n2)
        self.assertEqual(tuple(frappe.db.get_value("Lote de Pago", n2, ["secuencial", "nombre_archivo"])), (2, "170926-0002-06.txt"))
        marcar_transmitido(nombre, "119938")
        lote.reload()
        self.assertEqual((lote.estado_lote, lote.autorizacion_banco), ("Transmitido", "119938"))
        self.assertTrue(lote.transmitido_el)
        with self.assertRaises(frappe.ValidationError):   # regenerar un lote transmitido está bloqueado
            generar_archivo(nombre)
        # El saldo de las facturas no se movió
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa1.name, "outstanding_amount"), self.fa1.outstanding_amount)

    def test_regenerar_reemplaza_el_tef_y_respeta_los_demas_adjuntos(self):
        """BancaNet puede rechazar un archivo: mientras el lote no esté transmitido se vuelve a
        generar, con el mismo secuencial, sin dejar copias y sin borrar lo que Tesorería adjuntó."""
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        lote = frappe.get_doc("Lote de Pago", nombre); lote.submit()
        r1 = generar_archivo(nombre)
        frappe.get_doc({"doctype": "File", "file_name": "acuse.txt", "content": b"acuse", "is_private": 1,
                        "attached_to_doctype": "Lote de Pago", "attached_to_name": nombre}).insert(ignore_permissions=True)
        r2 = generar_archivo(nombre)
        self.assertEqual(r1["nombre_archivo"], r2["nombre_archivo"])
        self.assertEqual(frappe.db.get_value("Lote de Pago", nombre, "secuencial"), 1)
        adjuntos = frappe.get_all("File", filters={"attached_to_doctype": "Lote de Pago", "attached_to_name": nombre},
                                  pluck="file_name")
        self.assertEqual(sorted(adjuntos), sorted(["acuse.txt", r2["nombre_archivo"]]))

    def test_cancelar_libera_facturas(self):
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        lote = frappe.get_doc("Lote de Pago", nombre); lote.submit(); lote.reload(); lote.cancel()
        self.assertEqual(frappe.db.get_value("Lote de Pago", nombre, "estado_lote"), "Cancelado")
        self.assertFalse(frappe.db.get_value("Purchase Invoice", self.fa1.name, "en_lote"))
        self.assertEqual({f["name"] for f in facturas_pagables(pruebas_comun.EMPRESA)}, {self.fa1.name, self.fa2.name, self.fb.name})

    def test_no_se_cancela_un_lote_transmitido(self):
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        lote = frappe.get_doc("Lote de Pago", nombre); lote.submit(); generar_archivo(nombre); marcar_transmitido(nombre, "1")
        lote.reload()
        with self.assertRaises(frappe.ValidationError):
            lote.cancel()

    def test_los_campos_de_dinero_de_un_lote_enviado_no_se_editan_a_mano(self):
        """`read_only` es del formulario y `allow_on_submit` abre la escritura a cualquiera que pueda
        guardar el lote: sin guardia de servidor, Tesorería podía devolver a 'Exportado' un lote ya
        transmitido y volver a generar el archivo, o declarar aplicado un pago que el banco rechazó."""
        nombre = self._lote_transmitido()
        usuario(TESORERIA, "CxP Tesoreria")
        frappe.set_user(TESORERIA)
        lote = frappe.get_doc("Lote de Pago", nombre)
        lote.estado_lote = "Exportado"
        with self.assertRaises(frappe.ValidationError):
            lote.save()
        lote = frappe.get_doc("Lote de Pago", nombre)
        lote.transferencias[0].estado_pago = "Aplicado"
        with self.assertRaises(frappe.ValidationError):
            lote.save()
        lote = frappe.get_doc("Lote de Pago", nombre)
        lote.transferencias[0].importe = 1
        with self.assertRaises(frappe.ValidationError):
            lote.save()
        # Las notas sí: son el único campo que Tesorería escribe en un lote ya autorizado.
        lote = frappe.get_doc("Lote de Pago", nombre)
        lote.notas = "el banco pidió el archivo otra vez"
        lote.save()
        self.assertEqual(tuple(frappe.db.get_value("Lote de Pago", nombre, ["estado_lote", "notas"])),
                         ("Transmitido", "el banco pidió el archivo otra vez"))

    def test_no_se_cancela_un_lote_rechazado(self):
        """Un lote Rechazado ya se subió a BancaNet: cancelarlo liberaría facturas que el banco
        pudo haber pagado. El camino es 'Nuevo lote con los pendientes'."""
        nombre = self._lote_transmitido()
        frappe.db.set_value("Lote de Pago", nombre, "estado_lote", "Rechazado", update_modified=False)
        lote = frappe.get_doc("Lote de Pago", nombre)
        with self.assertRaises(frappe.ValidationError):
            lote.cancel()

    def test_un_fallo_al_reintentar_no_suelta_las_facturas(self):
        """El lote de reintento se arma e inserta PRIMERO y las facturas se liberan después: si algo
        falla, ninguna factura queda sin `en_lote` y sin lote que la reclame."""
        lotes = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100},
                                                           {"factura": self.fa2.name, "importe": 50}])
        lote = frappe.get_doc("Lote de Pago", lotes[0]); lote.submit()
        generar_archivo(lote.name); marcar_transmitido(lote.name, "119938")
        lote.reload(); lote.transferencias[0].db_set("estado_pago", "Rechazado"); lote.db_set("estado_lote", "Rechazado")
        with patch("gode_cxp.pagos.eventos.validar_nombre_tef", side_effect=RuntimeError("falla simulada")):
            with self.assertRaises(RuntimeError):
                nuevo_lote_pendientes(lote.name)
        for factura in (self.fa1.name, self.fa2.name):
            self.assertEqual(frappe.db.get_value("Purchase Invoice", factura, "en_lote"), lote.name)
        self.assertEqual(frappe.db.count("Lote de Pago", {"lote_origen": lote.name}), 0)
        # Y sin la falla el reintento sigue saliendo.
        self.assertTrue(nuevo_lote_pendientes(lote.name))

    def test_una_factura_apartada_por_otra_sesion_aborta_el_submit(self):
        """Dos personas armando lotes a la vez: entre el borrador y el submit otra sesión pudo
        apartar la factura. El submit se aborta y el lote se queda en borrador."""
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        frappe.db.set_value("Purchase Invoice", self.fa1.name, "en_lote", "LOTE-2026-9999")
        lote = frappe.get_doc("Lote de Pago", nombre)
        with self.assertRaises(frappe.ValidationError):
            lote.submit()
        self.assertEqual(frappe.db.get_value("Lote de Pago", nombre, "docstatus"), 0)

    def test_tesoreria_borra_borradores_pero_no_lotes_enviados(self):
        (borrador,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        (enviado,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fb.name, "importe": 100}])
        frappe.get_doc("Lote de Pago", enviado).submit()
        usuario(TESORERIA, "CxP Tesoreria")
        frappe.set_user(TESORERIA)
        frappe.delete_doc("Lote de Pago", borrador)
        self.assertFalse(frappe.db.exists("Lote de Pago", borrador))
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc("Lote de Pago", enviado)

    def test_un_borrador_de_otra_empresa_no_aparta_facturas(self):
        """`facturas_pagables` esconde las facturas que un lote borrador ya tiene apartadas, pero
        sólo las de la misma empresa: con dos empresas en el sitio, el borrador de una no puede
        desaparecer facturas de la otra."""
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        frappe.db.set_value("Lote de Pago", nombre, "company", OTRA_EMPRESA, update_modified=False)
        self.assertIn(self.fa1.name, {f["name"] for f in facturas_pagables(pruebas_comun.EMPRESA)})

    def test_la_cuenta_de_cargo_debe_ser_de_la_empresa_del_lote(self):
        cuenta = frappe.db.get_single_value("Configuracion CxP", "cuenta_bancaria_empresa")
        antes = frappe.db.get_value("Bank Account", cuenta, "company")
        frappe.db.set_value("Bank Account", cuenta, "company", OTRA_EMPRESA, update_modified=False)
        try:
            with self.assertRaises(frappe.ValidationError):
                crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        finally:
            frappe.db.set_value("Bank Account", cuenta, "company", antes, update_modified=False)

    def test_la_autorizacion_del_banco_es_un_numero(self):
        """El acuse de BancaNet es un número y se va tal cual al reporte para COI: si Tesorería pega
        otra cosa, el lote quedaría Transmitido con basura."""
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
        frappe.get_doc("Lote de Pago", nombre).submit(); generar_archivo(nombre)
        for malo in ("", "   ", "abc", "119938-A", "119 938", "1234567890123"):
            with self.assertRaises(frappe.ValidationError):
                marcar_transmitido(nombre, malo)
        marcar_transmitido(nombre, " 119938 ")
        self.assertEqual(tuple(frappe.db.get_value("Lote de Pago", nombre, ["estado_lote", "autorizacion_banco"])),
                         ("Transmitido", "119938"))

    def test_el_filtro_del_tef_anterior_no_busca_un_file_url_vacio(self):
        """Sin archivo previo el filtro sólo puede ir por nombre: `file_url = ""` casaría con
        cualquier adjunto del lote sin URL y generar_archivo se lo llevaría."""
        from gode_cxp.pagos.lotes import _filtros_del_tef_anterior
        self.assertEqual(_filtros_del_tef_anterior(None, "170926-0001-12.txt"),
                         [["file_name", "=", "170926-0001-12.txt"]])
        self.assertEqual(_filtros_del_tef_anterior("/private/files/viejo.txt", "170926-0001-12.txt"),
                         [["file_name", "=", "170926-0001-12.txt"], ["file_url", "=", "/private/files/viejo.txt"]])

    def test_referencia_numerica_fija(self):
        """Si Tesorería configura una referencia fija, es la que va al lote y al archivo (en lugar de
        la fecha de pago)."""
        frappe.db.set_single_value("Configuracion CxP", "referencia_numerica_modo", "Fija")
        frappe.db.set_single_value("Configuracion CxP", "referencia_numerica_fija", "1234567")
        try:
            (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}])
            self.assertEqual(frappe.db.get_value("Lote de Pago", nombre, "referencia_numerica"), "1234567")
            frappe.get_doc("Lote de Pago", nombre).submit()
            r = generar_archivo(nombre)
            contenido = frappe.get_doc("File", {"file_url": r["file_url"]}).get_content()
            leido = leer_tef(contenido if isinstance(contenido, bytes) else contenido.encode("ascii"))
            self.assertEqual(leido["referencia_numerica"], "1234567")
        finally:
            frappe.db.set_single_value("Configuracion CxP", "referencia_numerica_modo", "Fecha del lote")
            frappe.db.set_single_value("Configuracion CxP", "referencia_numerica_fija", None)

    def test_facturas_pagables_filtra_por_proveedor_y_por_vencimiento(self):
        self.assertEqual({f["name"] for f in facturas_pagables(pruebas_comun.EMPRESA, proveedor=self.fa1.supplier)},
                         {self.fa1.name, self.fa2.name})
        self.assertEqual(facturas_pagables(pruebas_comun.EMPRESA, hasta_vencimiento="2000-01-01"), [])
        self.assertEqual({f["name"] for f in facturas_pagables(pruebas_comun.EMPRESA, hasta_vencimiento="2100-01-01")},
                         {self.fa1.name, self.fa2.name, self.fb.name})

    def test_nuevo_lote_con_pendientes(self):
        lotes = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa1.name, "importe": 100}, {"factura": self.fa2.name, "importe": 50}])
        lote = frappe.get_doc("Lote de Pago", lotes[0]); lote.submit(); generar_archivo(lote.name); marcar_transmitido(lote.name, "1")
        # Simula lo que hará la Task 7: la transferencia rechazada y el lote en Rechazado
        lote.reload(); lote.transferencias[0].db_set("estado_pago", "Rechazado"); lote.db_set("estado_lote", "Rechazado")
        nuevo = nuevo_lote_pendientes(lote.name)
        n = frappe.get_doc("Lote de Pago", nuevo)
        self.assertEqual((n.lote_origen, n.estado_lote, n.docstatus, len(n.transferencias), len(n.facturas)), (lote.name, "Preparado", 0, 1, 2))
        self.assertIsNone(frappe.db.get_value("Purchase Invoice", self.fa1.name, "en_lote"))   # liberada hasta que se autorice el nuevo
        self.assertIsNone(nuevo_lote_pendientes(lote.name))   # ya no queda nada pendiente
