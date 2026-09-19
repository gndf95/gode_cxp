"""Lotes de pago: qué se puede pagar, cómo se agrupa, el archivo TEF, la transmisión y el reintento."""
from datetime import date

import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import cuenta_verificada, factura_aprobada, xml_con
from gode_cxp.pagos.lotes import crear_lotes, facturas_pagables, generar_archivo, marcar_transmitido, nuevo_lote_pendientes
from gode_cxp.pagos.tef import leer_tef
from gode_cxp.setup.produccion import configurar_pagos

CLABE_12 = "072180007090045065"
# La misma CLABE Banamex válida que en test_cuentas_bancarias (la del plan, ...901, trae el dígito
# verificador mal y validar_clabe la rechaza).
CLABE_06 = "002180012345678906"
FECHA = date(2026, 9, 17)


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
