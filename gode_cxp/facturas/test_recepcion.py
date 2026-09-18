import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas.recepcion import procesar_xml


def limpiar_cfdis():
    for name in frappe.get_all("CFDI Recibido", pluck="name"):
        frappe.delete_doc("CFDI Recibido", name, force=1, ignore_permissions=True)


class TestRecepcion(FrappeTestCase):
    def setUp(self):
        frappe.db.set_single_value("Configuracion CxP", "rfc_empresa", ejemplos.RFC_EMPRESA)
        limpiar_cfdis()

    def test_ingreso_crea_cfdi_recibido(self):
        doc = procesar_xml(ejemplos.INGRESO_40, "Carga manual", "A1234.xml")
        self.assertEqual(doc.doctype, "CFDI Recibido")
        self.assertEqual(doc.uuid, "6F2C3D48-1234-4A5B-9C8D-ABCDEF012345")
        self.assertEqual(doc.estado, "Nuevo")
        self.assertEqual(doc.tipo_comprobante, "I")
        self.assertEqual((doc.rfc_emisor, doc.nombre_emisor), ("AVI900101AB1", "AVICOLA DEL CARMEN SA DE CV"))
        self.assertEqual((doc.serie, doc.folio), ("A", "1234"))
        self.assertEqual((float(doc.subtotal), float(doc.iva_trasladado), float(doc.total)), (1000.0, 160.0, 1160.0))
        self.assertEqual((doc.metodo_pago, doc.forma_pago, doc.moneda), ("PPD", "03", "MXN"))
        self.assertEqual(len(doc.conceptos), 1)
        self.assertEqual(doc.conceptos[0].descripcion, "Pechuga de pollo")
        self.assertTrue(doc.archivo_xml and doc.archivo_xml.startswith("/private/files/"))
        self.assertEqual(doc.origen, "Carga manual")
        self.assertFalse(doc.flags.duplicado)

    def test_duplicado_no_se_inserta(self):
        primero = procesar_xml(ejemplos.INGRESO_40, "Carga manual")
        segundo = procesar_xml(ejemplos.INGRESO_40, "SAT")
        self.assertEqual(segundo.name, primero.name)
        self.assertTrue(segundo.flags.duplicado)
        self.assertEqual(frappe.db.count("CFDI Recibido", {"uuid": primero.uuid}), 1)

    def test_receptor_ajeno(self):
        doc = procesar_xml(ejemplos.AJENO_40, "SAT")
        self.assertEqual(doc.estado, "Ajeno")

    def test_pago_y_retenciones(self):
        p = procesar_xml(ejemplos.PAGO_40, "SAT")
        self.assertEqual((p.tipo_comprobante, p.estado), ("P", "Nuevo"))
        r = procesar_xml(ejemplos.INGRESO_33_RETENCIONES, "SAT")
        self.assertEqual((float(r.isr_retenido), float(r.iva_retenido), float(r.total)), (500.0, 533.33, 4766.67))
        self.assertEqual(r.version_cfdi, "3.3")

    def test_xml_invalido(self):
        with self.assertRaises(frappe.ValidationError):
            procesar_xml(ejemplos.SIN_TIMBRE, "Carga manual")

    def test_indice_unico_uuid(self):
        doc = procesar_xml(ejemplos.EGRESO_40, "SAT")
        copia = frappe.copy_doc(doc)
        with self.assertRaises(frappe.UniqueValidationError):
            copia.insert(ignore_permissions=True)

    def test_sin_rfc_empresa_no_procesa(self):
        frappe.db.set_single_value("Configuracion CxP", "rfc_empresa", "")
        try:
            with self.assertRaises(frappe.ValidationError):
                procesar_xml(ejemplos.INGRESO_40, "Carga manual")
        finally:
            frappe.db.set_single_value("Configuracion CxP", "rfc_empresa", ejemplos.RFC_EMPRESA)

    def test_pdf_se_adjunta(self):
        # Frappe valida que un adjunto con file_type PDF sea un PDF real (revisa JS embebido),
        # así que se necesita un PDF mínimo pero válido, no bytes cualesquiera con encabezado %PDF-.
        from io import BytesIO

        from pypdf import PdfWriter

        buf = BytesIO()
        PdfWriter().write(buf)
        pdf_bytes = buf.getvalue()

        doc = procesar_xml(ejemplos.INGRESO_40, "Correo", "A1234.xml", pdf_bytes=pdf_bytes)
        doc = frappe.get_doc("CFDI Recibido", doc.name)
        self.assertTrue(doc.archivo_xml)
        self.assertTrue(doc.archivo_pdf)
        self.assertTrue(doc.archivo_pdf.endswith(".pdf"))
        for url in (doc.archivo_xml, doc.archivo_pdf):
            self.assertEqual(frappe.db.get_value("File", {"file_url": url}, "is_private"), 1)

    def test_proveedor_por_rfc_existente(self):
        grupo = frappe.db.get_value("Supplier Group", {}, "name")
        proveedor = frappe.get_doc({
            "doctype": "Supplier", "supplier_name": "PRUEBA AVICOLA", "supplier_group": grupo,
            "tax_id": "AVI900101AB1",
        }).insert(ignore_permissions=True)
        try:
            doc = procesar_xml(ejemplos.INGRESO_40, "Carga manual")
            self.assertEqual(doc.proveedor, proveedor.name)
        finally:
            frappe.delete_doc("Supplier", proveedor.name, force=1, ignore_permissions=True)

    def test_campos_estandar_existen(self):
        for campo in ("tipo_persona", "nombre_pila", "apellido_paterno", "apellido_materno", "correo_avisos", "bloqueado_pagos", "motivo_bloqueo"):
            self.assertTrue(frappe.get_meta("Supplier").has_field(campo), campo)
        meta = frappe.get_meta("Purchase Invoice")
        for campo in ("cfdi_uuid", "cfdi_recibido", "metodo_pago_sat", "forma_pago_sat", "rfc_emisor", "estado_revision",
                      "recepcion_confirmada", "recepcion_confirmada_por", "recepcion_confirmada_el", "nota_aclaracion", "complemento_recibido"):
            self.assertTrue(meta.has_field(campo), campo)
        self.assertEqual(meta.get_field("cfdi_uuid").unique, 1)
