import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.crear_factura import crear_factura_desde_cfdi
from gode_cxp.facturas.proveedores import proveedor_por_rfc
from gode_cxp.facturas.recepcion import procesar_xml


class TestCrearFactura(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()

    def setUp(self):
        pruebas_comun.limpiar()

    def test_proveedor_por_rfc(self):
        name = proveedor_por_rfc("AVI900101AB1", "AVICOLA DEL CARMEN SA DE CV")
        sup = frappe.get_doc("Supplier", name)
        self.assertEqual((sup.tax_id, sup.tipo_persona, sup.supplier_type), ("AVI900101AB1", "Moral", "Company"))
        self.assertEqual(sup.supplier_group, "Proveedores CFDI")
        self.assertEqual(proveedor_por_rfc("AVI900101AB1", "OTRO NOMBRE"), name)     # mismo RFC → mismo proveedor
        fisica = frappe.get_doc("Supplier", proveedor_por_rfc("HESB850101AB1", "BRUNO RICARDO HERNANDEZ SILVA"))
        self.assertEqual((fisica.tipo_persona, fisica.supplier_type), ("Física", "Individual"))

    def test_ingreso_crea_factura_borrador(self):
        cfdi = procesar_xml(ejemplos.INGRESO_40, "Carga manual")
        pi_name = crear_factura_desde_cfdi(cfdi.name)
        pi = frappe.get_doc("Purchase Invoice", pi_name)
        self.assertEqual(pi.docstatus, 0)
        self.assertEqual(pi.supplier, proveedor_por_rfc("AVI900101AB1", ""))
        self.assertEqual((pi.bill_no, str(pi.bill_date)), ("A-1234", "2026-09-10"))
        self.assertEqual(str(pi.due_date), add_days("2026-09-10", 30))
        self.assertEqual((pi.cfdi_uuid, pi.cfdi_recibido, pi.rfc_emisor), (cfdi.uuid, cfdi.name, "AVI900101AB1"))
        self.assertEqual((pi.metodo_pago_sat, pi.forma_pago_sat), ("PPD", "03"))
        self.assertEqual(pi.estado_revision, "Recibida")
        self.assertEqual(len(pi.items), 1)
        self.assertEqual((pi.items[0].item_code, pi.items[0].qty, pi.items[0].rate), ("CFDI-CONCEPTO", 10, 100))
        self.assertEqual(pi.items[0].description, "Pechuga de pollo")
        self.assertEqual(len(pi.taxes), 1)
        self.assertEqual((pi.taxes[0].add_deduct_tax, round(pi.taxes[0].tax_amount, 2)), ("Add", 160.0))
        self.assertAlmostEqual(pi.grand_total, 1160.0, places=2)
        cfdi.reload()
        self.assertEqual((cfdi.estado, cfdi.factura), ("Con factura", pi_name))
        self.assertEqual(crear_factura_desde_cfdi(cfdi.name), pi_name)     # segunda llamada no duplica

    def test_retenciones(self):
        cfdi = procesar_xml(ejemplos.INGRESO_33_RETENCIONES, "SAT")
        pi = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(cfdi.name))
        deducciones = {t.description: round(t.tax_amount, 2) for t in pi.taxes if t.add_deduct_tax == "Deduct"}
        self.assertEqual(deducciones, {"ISR retenido": 500.0, "IVA retenido": 533.33})
        self.assertAlmostEqual(pi.grand_total, 4766.67, places=2)
        self.assertEqual(pi.estado_revision, "Recibida")

    def test_usd(self):
        cfdi = procesar_xml(ejemplos.USD_40, "SAT")
        pi = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(cfdi.name))
        self.assertEqual((pi.currency, pi.conversion_rate), ("USD", 18.5))
        self.assertAlmostEqual(pi.grand_total, 1160.0, places=2)
        self.assertAlmostEqual(pi.base_grand_total, 21460.0, places=2)

    def test_nota_de_credito(self):
        original = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(procesar_xml(ejemplos.INGRESO_40, "SAT").name))
        # Con el flujo de revisión activo la factura sólo se envía ya aprobada; Administrator tiene
        # todos los roles, así que puede aplicar las tres transiciones.
        original.db_set("recepcion_confirmada", 1)
        for accion in ("Enviar a revisión", "Confirmar recepción", "Aprobar"):
            apply_workflow(original, accion)
            original.reload()
        self.assertEqual((original.estado_revision, original.docstatus), ("Aprobada", 1))
        cfdi = procesar_xml(ejemplos.EGRESO_40, "SAT")
        nc = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(cfdi.name))
        self.assertEqual((nc.is_return, nc.return_against), (1, original.name))
        self.assertAlmostEqual(nc.grand_total, -232.0, places=2)
        self.assertEqual(nc.items[0].qty, -1)

    def test_cantidad_no_exacta(self):
        cfdi = procesar_xml(ejemplos.CANTIDAD_NO_EXACTA_40, "SAT")
        pi = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(cfdi.name))
        self.assertEqual(len(pi.items), 1)
        self.assertEqual((pi.items[0].qty, pi.items[0].rate), (1, 1000))
        self.assertIn("30 Kilogramo × 33.33", pi.items[0].description)
        self.assertAlmostEqual(pi.grand_total, 1160.0, places=2)
        self.assertEqual(pi.estado_revision, "Recibida")

    def test_nota_de_credito_con_retenciones(self):
        cfdi = procesar_xml(ejemplos.EGRESO_RETENCIONES_40, "SAT")
        nc = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(cfdi.name))
        self.assertEqual(nc.is_return, 1)
        self.assertAlmostEqual(nc.grand_total, -190.67, places=2)
        self.assertEqual(nc.estado_revision, "Recibida")

    def test_proveedor_nombre_repetido(self):
        uno = proveedor_por_rfc("AVI900101AB1", "AVICOLA DEL CARMEN SA DE CV")
        dos = proveedor_por_rfc("AVI900101AB2", "AVICOLA DEL CARMEN SA DE CV")
        self.assertNotEqual(uno, dos)
        self.assertIn("(AVI900101AB2)", frappe.get_doc("Supplier", dos).supplier_name)
        with self.assertRaises(frappe.ValidationError):
            proveedor_por_rfc("", "X")

    def test_total_que_no_cuadra(self):
        cfdi = procesar_xml(ejemplos.INGRESO_40, "SAT")
        frappe.db.set_value("CFDI Recibido", cfdi.name, "total", 1200)     # se simula un XML mal leído
        pi = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(cfdi.name))
        self.assertEqual(pi.estado_revision, "Error de lectura")
        self.assertIn("1200", pi.nota_aclaracion)

    def test_solo_ingresos_y_egresos(self):
        pago = procesar_xml(ejemplos.PAGO_40, "SAT")
        with self.assertRaises(frappe.ValidationError):
            crear_factura_desde_cfdi(pago.name)
        ajeno = procesar_xml(ejemplos.AJENO_40, "SAT")
        with self.assertRaises(frappe.ValidationError):
            crear_factura_desde_cfdi(ajeno.name)
