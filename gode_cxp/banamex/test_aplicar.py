"""Aplicar el resultado del banco: un Payment Entry por transferencia aplicada, los rechazos, la
idempotencia y el estado del lote.

Es lo único de la app que mueve saldos, así que las pruebas miran el efecto contable de verdad (el
`outstanding_amount` de cada factura) y no sólo los campos que se escriben.
"""
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.banamex import api
from gode_cxp.banamex.aplicar import _crear_pago, aplicar_resultado, crear_resultado_desde_lote
from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import cuenta_verificada, factura_aprobada, usuario, xml_con
from gode_cxp.pagos.lotes import crear_lotes, facturas_pagables, generar_archivo, marcar_transmitido, nuevo_lote_pendientes
from gode_cxp.setup.produccion import MODO_PAGO_TRANSFERENCIA, configurar_pagos

# La misma CLABE Banorte de test_lotes (naturaleza 12, interbancaria) para el proveedor A…
CLABE_A = "072180007090045065"
# …y una de BBVA para el tercer proveedor, para que las dos transferencias quepan en un solo lote
# (un lote es de una sola naturaleza) y el cruce por cuenta tenga dos candidatos distintos.
CLABE_C = "012180012345678909"
FECHA = date(2026, 9, 17)
AUTORIZACION = "119938"
MOTIVO = "VERIFIQUE CARACTERES INVALIDOS EN E"
TESORERIA, REVISOR = "prueba.tesoreria@cxp.local", "prueba.revisor@cxp.local"
# Un tercer proveedor: el mismo CFDI con otro RFC de emisor y otro nombre. El nombre también cambia
# porque el Supplier se nombra por `supplier_name` y dos proveedores con el mismo nombre chocarían.
TERCERO = (ejemplos.INGRESO_40
           .replace(b'Rfc="AVI900101AB1"', b'Rfc="AVI900101AB2"')
           .replace(b'Nombre="AVICOLA DEL CARMEN SA DE CV"', b'Nombre="COMERCIALIZADORA VARGAS SA DE CV"'))


class TestAplicar(FrappeTestCase):
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
        # Proveedor A con dos facturas de 1160 (una transferencia que cubre las dos, con importes
        # parciales) y un tercer proveedor interbancario con la suya.
        self.fa1 = factura_aprobada(ejemplos.INGRESO_40)
        self.fa2 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "11111111-2222-3333-4444-555555555555", "77"))
        self.fc = factura_aprobada(xml_con(TERCERO, "22222222-3333-4444-5555-666666666666", "78"))
        self.cta_a = cuenta_verificada(self.fa1.supplier, CLABE_A)
        self.cta_c = cuenta_verificada(self.fc.supplier, CLABE_C, banco="BBVA")
        self.lote12 = self._transmitido([{"factura": self.fa1.name, "importe": 1000},
                                         {"factura": self.fa2.name, "importe": 160}])

    def tearDown(self):
        frappe.set_user("Administrator")

    def _transmitido(self, partidas, autorizacion=AUTORIZACION):
        """Un lote con esas partidas, autorizado, con su archivo generado y ya subido al banco."""
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, partidas)
        frappe.get_doc("Lote de Pago", nombre).submit()
        generar_archivo(nombre)
        marcar_transmitido(nombre, autorizacion)
        return frappe.get_doc("Lote de Pago", nombre)

    def _resultado(self, lote, estatus_por_linea, importes=None):
        """Lo que Tesorería captura del portal: el 3 / 5 de cada línea y su clave de rastreo."""
        r = crear_resultado_desde_lote(lote.name)
        for m in r.movimientos:
            m.estatus = estatus_por_linea.get(m.linea, "")
            if importes and m.linea in importes:
                m.importe = importes[m.linea]
            if m.estatus == "3":
                m.clave_rastreo = f"RASTREO{m.linea}"
            elif m.estatus == "5":
                m.motivo = MOTIVO
        r.save()
        return r

    def _solo_ve(self, doctype, valor, correo=TESORERIA):
        """Le deja a `correo` un único documento permitido de `doctype`, o sea le niega los demás
        (misma técnica que banamex/test_respuesta.py)."""
        up = frappe.get_doc({"doctype": "User Permission", "user": correo, "allow": doctype, "for_value": valor})
        up.flags.ignore_links = True
        up.insert(ignore_permissions=True)
        self.addCleanup(frappe.clear_cache, user=correo)
        self.addCleanup(frappe.delete_doc, "User Permission", up.name, force=1, ignore_permissions=True)
        frappe.clear_cache(user=correo)

    def test_aplicado_crea_un_pago_por_transferencia_con_sus_facturas(self):
        r = self._resultado(self.lote12, {1: "3"})
        out = aplicar_resultado(r.name)
        self.assertEqual(len(out["creados"]), 1, out["resumen"])
        pe = frappe.get_doc("Payment Entry", out["creados"][0])
        self.assertEqual((pe.docstatus, pe.payment_type, pe.party, pe.paid_amount, pe.mode_of_payment),
                         (1, "Pay", self.fa1.supplier, 1160.0, MODO_PAGO_TRANSFERENCIA))
        # Una asignación exacta por factura: es lo que get_payment_entry no sabe hacer.
        self.assertEqual({(x.reference_name, x.allocated_amount) for x in pe.references},
                         {(self.fa1.name, 1000.0), (self.fa2.name, 160.0)})
        self.assertEqual((pe.lote_pago, pe.autorizacion_banco, pe.clave_rastreo, pe.reference_no),
                         (self.lote12.name, AUTORIZACION, "RASTREO1", f"{AUTORIZACION}-0001-001"))
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa1.name, "outstanding_amount"),
                         self.fa1.outstanding_amount - 1000)
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa2.name, "outstanding_amount"),
                         self.fa2.outstanding_amount - 160)
        self.lote12.reload()
        self.assertEqual((self.lote12.estado_lote, self.lote12.transferencias[0].estado_pago,
                          self.lote12.transferencias[0].pago), ("Aplicado", "Aplicado", pe.name))
        r.reload()
        self.assertEqual((r.estado, r.movimientos[0].accion, r.movimientos[0].pago),
                         ("Aplicado", "Pago creado", pe.name))
        self.assertTrue(r.aplicado_el and r.aplicado_por)

    def test_reaplicar_no_duplica(self):
        """La idempotencia que evita el pago doble: una transferencia ya Aplicado no se vuelve a pagar
        ni desde otro resultado del mismo lote."""
        r = self._resultado(self.lote12, {1: "3"})
        aplicar_resultado(r.name)
        r2 = self._resultado(self.lote12, {1: "3"})
        api.marcar_revisado(r2.name)
        out = aplicar_resultado(r2.name)
        self.assertEqual((out["creados"], out["ya_aplicados"]), ([], 1))
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name, "docstatus": 1}), 1)
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa1.name, "outstanding_amount"),
                         self.fa1.outstanding_amount - 1000)

    def test_rechazado_deja_la_factura_pendiente_y_permite_reintento(self):
        r = self._resultado(self.lote12, {1: "5"})
        out = aplicar_resultado(r.name)
        self.assertEqual((out["creados"], out["rechazados"]), ([], 1))
        self.lote12.reload()
        self.assertEqual((self.lote12.estado_lote, self.lote12.transferencias[0].estado_pago,
                          self.lote12.transferencias[0].motivo_rechazo),
                         ("Rechazado", "Rechazado", MOTIVO))
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa1.name, "outstanding_amount"),
                         self.fa1.outstanding_amount)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 0)
        nuevo = nuevo_lote_pendientes(self.lote12.name)
        self.assertEqual(len(frappe.get_doc("Lote de Pago", nuevo).transferencias), 1)

    def test_parcial_y_diferencia_de_resumen(self):
        """Un lote de dos transferencias: la del proveedor A aplicada y la del tercero rechazada. El
        total de control del archivo no cuadra con el del lote, así que además queda Con diferencias."""
        fa3 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "33333333-4444-5555-6666-777777777777", "79"))
        lote = self._transmitido([{"factura": fa3.name, "importe": 100},
                                  {"factura": self.fc.name, "importe": 50}], autorizacion="1")
        r = self._resultado(lote, {1: "3", 2: "5"})
        r.total_archivo = 999
        r.save()
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        lote.reload()
        r.reload()
        self.assertEqual((lote.estado_lote, len(out["creados"]), out["rechazados"], r.estado),
                         ("Parcial", 1, 1, "Con diferencias"))
        self.assertIn("total", r.diferencias)
        self.assertEqual([t.estado_pago for t in lote.transferencias], ["Aplicado", "Rechazado"])
        self.assertEqual(frappe.db.get_value("Purchase Invoice", fa3.name, "outstanding_amount"),
                         fa3.outstanding_amount - 100)
        self.assertIsNone(frappe.db.get_value("Purchase Invoice", fa3.name, "en_lote"))
        self.assertIn(fa3.name, {f["name"] for f in facturas_pagables(pruebas_comun.EMPRESA)})
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fc.name, "outstanding_amount"),
                         self.fc.outstanding_amount)

    def test_importe_distinto_no_se_aplica(self):
        """Si el banco reporta otro importe no se paga a ciegas: no se crea nada y queda anotado."""
        r = self._resultado(self.lote12, {1: "3"}, importes={1: 1159.0})
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        r.reload()
        self.assertEqual((out["creados"], r.movimientos[0].accion), ([], "Importe distinto"))
        self.assertEqual(frappe.db.get_value("Lote de Pago", self.lote12.name, "estado_lote"), "Transmitido")
        self.assertEqual(r.estado, "Con diferencias")

    def test_sin_coincidencia(self):
        r = self._resultado(self.lote12, {1: "3"})
        r.movimientos[0].linea = 9
        r.movimientos[0].cuenta = "000000000000000000"
        r.save()
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        self.assertEqual((out["creados"], out["sin_coincidencia"]), ([], 1))
        self.assertEqual(frappe.db.get_value("Lote de Pago", self.lote12.name, "estado_lote"), "Transmitido")

    def test_el_cruce_por_cuenta_e_importe_cuando_la_linea_no_cuadra(self):
        """El banco puede devolver el consecutivo de su propio archivo: si la línea no cruza, se busca
        por (cuenta, importe) y sólo se acepta si hay un único candidato."""
        fa3 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "44444444-5555-6666-7777-888888888888", "80"))
        lote = self._transmitido([{"factura": fa3.name, "importe": 100},
                                  {"factura": self.fc.name, "importe": 50}], autorizacion="1")
        r = self._resultado(lote, {1: "3", 2: "3"})
        for m in r.movimientos:
            m.linea = m.linea + 100        # ninguna línea existe en el lote
        r.save()
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        lote.reload()
        self.assertEqual((len(out["creados"]), lote.estado_lote), (2, "Aplicado"))
        self.assertEqual([t.estado_pago for t in lote.transferencias], ["Aplicado", "Aplicado"])

    def test_cancelar_el_pago_devuelve_la_transferencia_a_pendiente(self):
        """Cancelar el Payment Entry es la única forma de deshacer un pago: la transferencia vuelve a
        Pendiente, el saldo de las facturas vuelve y el lote deja de estar Aplicado."""
        (pago,) = aplicar_resultado(self._resultado(self.lote12, {1: "3"}).name)["creados"]
        frappe.get_doc("Payment Entry", pago).cancel()
        self.lote12.reload()
        t = self.lote12.transferencias[0]
        self.assertEqual((self.lote12.estado_lote, t.estado_pago, t.pago, t.clave_rastreo),
                         ("Transmitido", "Pendiente", None, None))
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa1.name, "outstanding_amount"),
                         self.fa1.outstanding_amount)
        # El lote vuelve a apartar sus facturas: al quedar Aplicado se habían liberado por tener saldo.
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fa1.name, "en_lote"), self.lote12.name)
        # Y se puede volver a aplicar, porque la transferencia está otra vez pendiente.
        r = self._resultado(self.lote12, {1: "3"})
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        self.assertEqual(len(out["creados"]), 1, out["resumen"])

    def test_el_lote_aplicado_libera_las_facturas_que_quedaron_con_saldo(self):
        """Un pago parcial deja la factura con saldo: cuando el lote queda Aplicado completo tiene que
        poder entrar a otro lote. La que quedó en cero conserva su `en_lote` como rastro."""
        aplicar_resultado(self._resultado(self.lote12, {1: "3"}).name)
        self.assertEqual(frappe.db.get_value("Lote de Pago", self.lote12.name, "estado_lote"), "Aplicado")
        for factura in (self.fa1.name, self.fa2.name):
            self.assertIsNone(frappe.db.get_value("Purchase Invoice", factura, "en_lote"),
                              f"{factura} quedó con saldo: tiene que quedar libre")
        self.assertIn(self.fa1.name, {f["name"] for f in facturas_pagables(pruebas_comun.EMPRESA)})
        completo = self._transmitido([{"factura": self.fc.name, "importe": self.fc.outstanding_amount}],
                                     autorizacion="2")
        aplicar_resultado(self._resultado(completo, {1: "3"}).name)
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fc.name, "outstanding_amount"), 0)
        self.assertEqual(frappe.db.get_value("Purchase Invoice", self.fc.name, "en_lote"), completo.name)

    def test_el_banco_dice_rechazado_pero_ya_hay_pago(self):
        """No se degrada un pago que ya existe: eso sólo lo hace cancelar el Payment Entry."""
        (pago,) = aplicar_resultado(self._resultado(self.lote12, {1: "3"}).name)["creados"]
        r = self._resultado(self.lote12, {1: "5"})
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        r.reload()
        self.assertEqual((out["rechazados"], out["ya_aplicados"], r.movimientos[0].accion),
                         (0, 1, "Ya aplicado"))
        self.assertEqual(frappe.db.get_value("Lote de Pago Transferencia",
                                             self.lote12.transferencias[0].name, "estado_pago"), "Aplicado")
        self.assertIn(pago, r.diferencias)
        self.assertEqual(r.estado, "Con diferencias")

    def test_una_linea_sin_estatus_no_hace_nada(self):
        r = self._resultado(self.lote12, {})
        out = aplicar_resultado(r.name)
        self.assertEqual((out["creados"], out["rechazados"], out["sin_coincidencia"]), ([], 0, 0))
        self.assertEqual(frappe.db.get_value("Lote de Pago", self.lote12.name, "estado_lote"), "Transmitido")

    def test_no_se_aplican_los_pagos_de_un_lote_sin_transmitir(self):
        r = self._resultado(self.lote12, {1: "3"})
        frappe.db.set_value("Lote de Pago", self.lote12.name, "estado_lote", "Exportado", update_modified=False)
        with self.assertRaisesRegex(frappe.ValidationError, "transmitido"):
            aplicar_resultado(r.name)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 0)

    def test_solo_tesoreria_aplica(self):
        usuario(REVISOR, "CxP Revisor")
        r = self._resultado(self.lote12, {1: "3"})
        frappe.set_user(REVISOR)
        with self.assertRaises(frappe.PermissionError):
            api.aplicar(r.name)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 0)

    def test_el_permiso_sobre_el_lote_tambien_cuenta(self):
        """El rol es global; las User Permissions por empresa sólo se aplican mirando el documento.
        Sin esto, quien tuviera el rol de Tesorería podía pagar el lote de otra empresa."""
        usuario(TESORERIA, "CxP Tesoreria")
        r = self._resultado(self.lote12, {1: "3"})
        self._solo_ve("Lote de Pago", "LOTE-QUE-NO-ES-ESTE")
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.PermissionError):
            api.aplicar(r.name)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 0)

    def test_un_movimiento_que_ya_tiene_pago_no_se_edita(self):
        """Editar la línea de un pago ya creado no deshace el pago: sólo deja el resultado mintiendo
        sobre lo que pasó con el dinero."""
        r = self._resultado(self.lote12, {1: "3"})
        aplicar_resultado(r.name)
        for campo, valor in (("estatus", "5"), ("importe", 1.0), ("linea", 7), ("cuenta", "0000")):
            with self.subTest(campo=campo):
                doc = frappe.get_doc("Resultado Bancario", r.name)
                doc.movimientos[0].set(campo, valor)
                with self.assertRaisesRegex(frappe.ValidationError, "ya tiene el pago"):
                    doc.save()

    def test_un_movimiento_con_pago_no_se_puede_quitar_del_resultado(self):
        r = self._resultado(self.lote12, {1: "3"})
        aplicar_resultado(r.name)
        doc = frappe.get_doc("Resultado Bancario", r.name)
        doc.set("movimientos", [])
        doc.append("movimientos", {"linea": 1, "cuenta": CLABE_A, "importe": 1160, "estatus": "3"})
        with self.assertRaisesRegex(frappe.ValidationError, "ya tiene el pago"):
            doc.save()

    def test_el_segundo_resultado_avisa_que_el_lote_ya_tiene_uno_aplicado(self):
        """Un segundo archivo del banco sobre el mismo lote está permitido (el banco puede mandar una
        corrección), pero el resultado lo dice: lo que ya se pagó no se vuelve a pagar."""
        r = self._resultado(self.lote12, {1: "3"})
        aplicar_resultado(r.name)
        r2 = self._resultado(self.lote12, {1: "3"})
        self.assertEqual(r2.estado, "Con diferencias")
        self.assertIn(r.name, r2.diferencias)

    def test_tesoreria_aplica_y_recibe_el_resumen(self):
        """El contrato que consume el botón del formulario."""
        usuario(TESORERIA, "CxP Tesoreria")
        r = self._resultado(self.lote12, {1: "3"})
        frappe.set_user(TESORERIA)
        out = api.aplicar(r.name)
        self.assertEqual((len(out["creados"]), out["estado_lote"]), (1, "Aplicado"))
        self.assertIn("Aplicado", out["resumen"])

    def test_archivo_rechazado_o_cancelado_no_paga_lineas_aplicadas(self):
        for estatus in ("32", "10"):
            with self.subTest(estatus=estatus):
                r = self._resultado(self.lote12, {1: "3"})
                r.estatus_archivo = estatus
                r.save()
                api.marcar_revisado(r.name)
                with self.assertRaisesRegex(frappe.ValidationError, "rechazado/cancelado.*línea 1"):
                    aplicar_resultado(r.name)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 0)

    def test_crear_pago_exige_asignaciones_exactas(self):
        for importes in ([], [100], [1161], [1000, 159]):
            with self.subTest(importes=importes):
                facturas = [frappe._dict(importe=importe) for importe in importes]
                with patch("gode_cxp.banamex.aplicar.frappe.new_doc") as nuevo:
                    with self.assertRaisesRegex(frappe.ValidationError, "asignaciones"):
                        _crear_pago(self.lote12, self.lote12.transferencias[0], facturas,
                                    None, None, AUTORIZACION)
                    nuevo.assert_not_called()

    def test_diferencias_sin_pagos_exigen_revision(self):
        r = self._resultado(self.lote12, {1: "3"}, importes={1: 1159})
        with self.assertRaisesRegex(frappe.ValidationError, "Marcar revisado"):
            aplicar_resultado(r.name)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 0)
        api.marcar_revisado(r.name)
        self.assertEqual(aplicar_resultado(r.name)["creados"], [])

    def test_resultado_aplicado_no_admite_otra_aplicacion(self):
        r = self._resultado(self.lote12, {1: "3"})
        aplicar_resultado(r.name)
        with self.assertRaisesRegex(frappe.ValidationError, "Solo se aplica un resultado"):
            aplicar_resultado(r.name)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 1)

    def test_fecha_futura_no_crea_pagos(self):
        r = self._resultado(self.lote12, {1: "3"})
        with patch("gode_cxp.banamex.aplicar.today", return_value="2026-09-16"):
            with self.assertRaisesRegex(frappe.ValidationError, "el pago aún no ocurre"):
                aplicar_resultado(r.name)
        self.assertEqual(frappe.db.count("Payment Entry", {"lote_pago": self.lote12.name}), 0)

    def test_error_al_pagar_conserva_el_motivo_del_banco(self):
        r = self._resultado(self.lote12, {1: "3"})
        r.movimientos[0].motivo = MOTIVO
        r.save()
        with patch("gode_cxp.banamex.aplicar._crear_pago", side_effect=frappe.ValidationError("fallo simulado")):
            out = aplicar_resultado(r.name)
        r.reload()
        self.assertEqual(out["creados"], [])
        self.assertEqual(r.movimientos[0].accion, "Error al pagar")
        self.assertEqual(r.movimientos[0].motivo, MOTIVO + " | error al crear el pago: fallo simulado")
        self.assertEqual(r.estado, "Con diferencias")
        # Guardar las notas no borra el estado que dejó el intento de aplicación.
        r.save()
        self.assertEqual(r.estado, "Con diferencias")

    def test_resultado_con_aplicacion_congela_lote_y_estatus_archivo(self):
        r = self._resultado(self.lote12, {1: "3"})
        aplicar_resultado(r.name)
        otro = self._transmitido([{"factura": self.fc.name, "importe": 50}], autorizacion="2")
        for campo, valor in (("lote", otro.name), ("estatus_archivo", "32")):
            with self.subTest(campo=campo):
                r.reload()
                r.set(campo, valor)
                with self.assertRaisesRegex(frappe.ValidationError, "no se puede cambiar"):
                    r.save()

    def test_transferencia_reintentada_no_se_paga_desde_el_origen(self):
        aplicar_resultado(self._resultado(self.lote12, {1: "5"}).name)
        nuevo = nuevo_lote_pendientes(self.lote12.name)
        r = self._resultado(self.lote12, {1: "3"})
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        r.reload()
        self.assertEqual((out["creados"], out["ya_aplicados"]), ([], 1))
        self.assertEqual(r.movimientos[0].accion, "Ya aplicado")
        self.assertIn(nuevo, r.diferencias)

    def test_dos_lineas_para_la_misma_transferencia_no_duplican_pago(self):
        r = self._resultado(self.lote12, {1: "3"})
        r.append("movimientos", {"linea": 1, "cuenta": CLABE_A, "importe": 1160, "estatus": "3"})
        r.save()
        api.marcar_revisado(r.name)
        out = aplicar_resultado(r.name)
        self.assertEqual((len(out["creados"]), out["ya_aplicados"]), (1, 1))

    def test_aplicar_ve_el_commit_de_una_segunda_conexion(self):
        try:
            import pymysql
        except ImportError:
            self.skipTest("El runner no tiene pymysql para abrir una segunda conexión MariaDB.")
        try:
            segunda = pymysql.connect(
                host=frappe.conf.db_host or "localhost", port=int(frappe.conf.db_port or 3306),
                user=frappe.conf.db_user or frappe.conf.db_name,
                password=frappe.conf.db_password, database=frappe.conf.db_name,
                unix_socket=frappe.conf.db_socket or None, connect_timeout=5)
        except pymysql.MySQLError as error:
            self.skipTest("El runner no permite una segunda conexión MariaDB: " + type(error).__name__)
        pendiente = None
        try:
            fa3 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "55555555-6666-7777-8888-999999999999", "81"))
            lote = self._transmitido([{"factura": fa3.name, "importe": 100},
                                      {"factura": self.fc.name, "importe": 50}], autorizacion="2")
            aplicar_resultado(self._resultado(lote, {1: "3"}).name)
            r = self._resultado(lote, {2: "3"})
            api.marcar_revisado(r.name)
            frappe.db.commit()
            lote.reload()
            self.assertEqual(lote.estado_lote, "Parcial")
            pendiente = lote.transferencias[1].name
            anterior = (lote.transferencias[1].estado_pago, lote.transferencias[1].pago)
            filtros = {"lote_pago": lote.name, "docstatus": 1}
            cantidad = frappe.db.count("Payment Entry", filtros)
            with segunda.cursor() as cursor:
                cursor.execute("""update `tabLote de Pago Transferencia`
                                  set estado_pago='Aplicado', pago='PAGO-FICTICIO' where name=%s""",
                               (pendiente,))
            segunda.commit()
            self.assertEqual(frappe.db.get_value("Lote de Pago Transferencia", pendiente, "estado_pago"),
                             "Pendiente")
            try:
                # El pago ficticio sólo representa el commit ajeno; no existe para validar el Link.
                with patch("frappe.model.document.Document._validate_links"):
                    out = aplicar_resultado(r.name)
            except frappe.ValidationError as error:
                self.assertIn("Otra persona", str(error))
            else:
                self.assertEqual((out["creados"], out["ya_aplicados"]), ([], 1))
                r.reload()
                self.assertEqual(r.movimientos[1].accion, "Ya aplicado")
                self.assertEqual(r.movimientos[1].pago, "PAGO-FICTICIO")
            self.assertEqual(frappe.db.count("Payment Entry", filtros), cantidad)
        finally:
            frappe.db.rollback()
            try:
                if pendiente:
                    with segunda.cursor() as cursor:
                        cursor.execute("""update `tabLote de Pago Transferencia`
                                          set estado_pago=%s, pago=%s where name=%s""",
                                       (*anterior, pendiente))
                    segunda.commit()
            finally:
                segunda.close()

    def test_aplicar_recarga_movimientos_antes_del_cruce(self):
        viejo = self._resultado(self.lote12, {1: "3"})
        actual = frappe.get_doc("Resultado Bancario", viejo.name)
        actual.movimientos[0].estatus = "5"
        actual.movimientos[0].motivo = MOTIVO
        actual.save()
        obtener = frappe.get_doc
        entregado = False

        def documento(*args, **kwargs):
            nonlocal entregado
            if not entregado and args == ("Resultado Bancario", viejo.name):
                entregado = True
                return viejo
            return obtener(*args, **kwargs)

        # Simula el documento que se leyó antes de esperar por el candado del lote.
        with patch("gode_cxp.banamex.aplicar.frappe.get_doc", side_effect=documento):
            out = aplicar_resultado(viejo.name)
        self.assertEqual((out["creados"], out["rechazados"]), ([], 1))
        actual.reload()
        self.assertEqual(actual.movimientos[0].accion, "Rechazado")

    def test_diferencias_con_pagos_previos_permiten_completar(self):
        fa3 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "66666666-7777-8888-9999-111111111111", "82"))
        lote = self._transmitido([{"factura": fa3.name, "importe": 100},
                                  {"factura": self.fc.name, "importe": 50}], autorizacion="2")
        r = self._resultado(lote, {1: "3"})
        r.total_archivo = 999
        r.save()
        api.marcar_revisado(r.name)
        self.assertEqual(len(aplicar_resultado(r.name)["creados"]), 1)
        r.reload()
        r.movimientos[1].estatus = "3"
        r.save()
        self.assertEqual(r.estado, "Con diferencias")
        out = aplicar_resultado(r.name)
        self.assertEqual((len(out["creados"]), out["ya_aplicados"]), (1, 1))
