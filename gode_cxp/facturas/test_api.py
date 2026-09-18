"""Carga manual desde la bandeja: XML sueltos, ZIP con pares XML/PDF y el botón de crear factura."""
import io
import zipfile
from io import BytesIO

import frappe
from frappe.tests.utils import FrappeTestCase
from pypdf import PdfWriter

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import api, pruebas_comun
from gode_cxp.facturas.api import crear_factura, procesar_archivos


def subir(nombre, contenido):
    """Deja el archivo en el sitio como lo haría el subidor del escritorio: un File sin adjuntar."""
    f = frappe.get_doc({"doctype": "File", "file_name": nombre, "content": contenido, "is_private": 1})
    f.insert(ignore_permissions=True)
    return f.file_url


def pdf_de_verdad():
    """File.check_content rechaza los bytes que dicen ser PDF y no lo son, así que hace falta uno real."""
    buf = BytesIO()
    PdfWriter().write(buf)
    return buf.getvalue()


def zip_con(miembros):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nombre, contenido in miembros:
            z.writestr(nombre, contenido)
    return buf.getvalue()


class TestApi(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()

    def setUp(self):
        frappe.set_user("Administrator")
        pruebas_comun.limpiar()

    def test_varios_xml(self):
        urls = [subir("a.xml", ejemplos.INGRESO_40), subir("b.xml", ejemplos.INGRESO_33_RETENCIONES), subir("a2.xml", ejemplos.INGRESO_40),
                subir("ajeno.xml", ejemplos.AJENO_40), subir("malo.xml", b"<otro/>")]
        r = procesar_archivos(urls)
        self.assertEqual(len(r["nuevos"]), 3)          # ingreso, retenciones y ajeno se registran
        self.assertEqual(len(r["duplicados"]), 1)
        self.assertEqual(len(r["ajenos"]), 1)
        self.assertEqual(len(r["errores"]), 1)
        self.assertIn("malo.xml", r["errores"][0]["archivo"])
        self.assertEqual(len(r["facturas"]), 2)        # solo los dos ingresos propios generan factura
        # Frappe le da el mismo file_url a dos subidas con el mismo contenido (a.xml y a2.xml), así
        # que no basta con borrar la fila que se leyó: no debe quedar NINGUNA fila suelta del lote.
        sueltos = frappe.get_all("File", filters={"file_url": ["in", urls], "attached_to_doctype": ["in", ["", None]]},
                                 fields=["name", "file_name", "file_url"])
        self.assertEqual(sueltos, [], "los archivos temporales de la carga se borran")

    def test_zip_con_xml_y_pdf(self):
        contenido = zip_con([("carpeta/F77.xml", ejemplos.INGRESO_33_RETENCIONES),
                             ("carpeta/F77.pdf", pdf_de_verdad()),
                             ("leeme.txt", b"ignorar")])
        r = procesar_archivos([subir("lote.zip", contenido)])
        self.assertEqual(len(r["nuevos"]), 1)
        cfdi = frappe.get_doc("CFDI Recibido", r["nuevos"][0])
        self.assertTrue(cfdi.archivo_pdf and cfdi.archivo_pdf.endswith(".pdf"))
        self.assertEqual(cfdi.estado, "Con factura")

    def test_crear_factura_api(self):
        r = procesar_archivos([subir("p.xml", ejemplos.PAGO_40)])
        self.assertEqual(r["facturas"], [])
        with self.assertRaises(frappe.ValidationError):
            crear_factura(r["nuevos"][0])

    # ------------------------------------------------------------- seguridad

    def test_solo_archivos_registrados_en_el_sitio(self):
        """Nada de rutas sueltas: el file_url tiene que ser el de un File que ya existe."""
        r = procesar_archivos(["/private/files/../../../etc/passwd", "/private/files/no-existe.xml", "/etc/passwd"])
        self.assertEqual(len(r["errores"]), 3)
        self.assertEqual(r["nuevos"], [])

    def test_el_zip_no_se_sale_de_su_carpeta(self):
        """Los miembros con '..' o ruta absoluta se ignoran; el resto sí se procesa."""
        contenido = zip_con([("../fuera.xml", ejemplos.INGRESO_40), ("buena.xml", ejemplos.INGRESO_33_RETENCIONES)])
        r = procesar_archivos([subir("rutas.zip", contenido)])
        self.assertEqual(len(r["nuevos"]), 1)
        self.assertEqual(frappe.db.get_value("CFDI Recibido", r["nuevos"][0], "folio"), "77")

    def test_el_zip_tiene_tope_de_entradas(self):
        contenido = zip_con([(f"f{i}.xml", b"<x/>") for i in range(api.MAX_ENTRADAS_ZIP + 1)])
        r = procesar_archivos([subir("bomba.zip", contenido)])
        self.assertEqual(r["nuevos"], [])
        self.assertEqual(len(r["errores"]), 1)
        self.assertIn("bomba.zip", r["errores"][0]["archivo"])
