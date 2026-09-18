"""Carga manual desde la bandeja: XML sueltos, ZIP con pares XML/PDF y el botón de crear factura."""
import io
import zipfile

import frappe
from frappe.tests.utils import FrappeTestCase
from pypdf import PdfWriter

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import api, pruebas_comun
from gode_cxp.facturas.api import crear_factura, procesar_archivos

SOLO_LECTURA = "prueba.conta@cxp.local"   # CxP Contabilidad: ve la bandeja pero no puede cargar nada


def subir(nombre, contenido, privado=1):
    """Deja el archivo en el sitio como lo haría el subidor del escritorio: un File sin adjuntar."""
    f = frappe.get_doc({"doctype": "File", "file_name": nombre, "content": contenido, "is_private": privado})
    f.insert(ignore_permissions=True)
    return f.file_url


def pdf_de_verdad():
    """File.check_content rechaza los bytes que dicen ser PDF y no lo son, así que hace falta uno real."""
    buf = io.BytesIO()
    PdfWriter().write(buf)
    return buf.getvalue()


def zip_con(miembros):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nombre, contenido in miembros:
            z.writestr(nombre, contenido)
    return buf.getvalue()


def usuario(correo, rol):
    if not frappe.db.exists("User", correo):
        u = frappe.get_doc({"doctype": "User", "email": correo, "first_name": correo.split("@")[0], "send_welcome_email": 0})
        u.append("roles", {"role": rol})
        u.insert(ignore_permissions=True)
    return correo


class TestApi(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()

    def setUp(self):
        frappe.set_user("Administrator")
        pruebas_comun.limpiar()

    def tearDown(self):
        frappe.set_user("Administrator")

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
        self.assertEqual(r["con_error"], [])           # ninguna factura quedó en "Error de lectura"
        # Frappe le da el mismo file_url a dos subidas con el mismo contenido (a.xml y a2.xml), así
        # que no basta con borrar la fila que se leyó: no debe quedar NINGUNA fila suelta del lote.
        sueltos = frappe.get_all("File", filters={"file_url": ["in", urls], "attached_to_doctype": ["is", "not set"]},
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

    def test_solo_los_archivos_que_subio_el_usuario(self):
        """Un File público, o de otro usuario, ni se lee ni se borra: no es de esta carga."""
        publico = subir("publico.xml", ejemplos.INGRESO_40, privado=0)
        de_otro = subir("de-otro.xml", ejemplos.INGRESO_33_RETENCIONES)
        fila_de_otro = frappe.db.get_value("File", {"file_url": de_otro}, "name")
        frappe.db.set_value("File", fila_de_otro, "owner", "Guest")
        r = procesar_archivos([publico, de_otro])
        self.assertEqual(r["nuevos"], [])
        self.assertEqual(len(r["errores"]), 2)
        self.assertTrue(frappe.db.exists("File", {"file_url": publico}), "el archivo público sigue ahí")
        self.assertTrue(frappe.db.exists("File", fila_de_otro), "el archivo de otro usuario sigue ahí")

    def test_origen_invalido(self):
        with self.assertRaises(frappe.ValidationError):
            procesar_archivos([], origen="Lo que se me ocurra")

    def test_quien_solo_consulta_no_puede_cargar(self):
        usuario(SOLO_LECTURA, "CxP Contabilidad")
        frappe.set_user(SOLO_LECTURA)
        with self.assertRaises(frappe.PermissionError):
            procesar_archivos([])

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

    def test_el_zip_no_aplana_las_carpetas(self):
        """Dos CFDI distintos con el mismo nombre en carpetas distintas son dos archivos, y el PDF de
        una carpeta no se le adjunta al XML de otra."""
        contenido = zip_con([("ENE/f.xml", ejemplos.INGRESO_40),
                             ("FEB/f.xml", ejemplos.INGRESO_33_RETENCIONES),
                             ("A/F1.xml", ejemplos.AJENO_40),
                             ("B/F1.pdf", pdf_de_verdad())])
        r = procesar_archivos([subir("carpetas.zip", contenido)])
        self.assertEqual(len(r["nuevos"]), 3)
        self.assertEqual(len(r["facturas"]), 2)
        ajenos = [n for n in r["nuevos"] if frappe.db.get_value("CFDI Recibido", n, "estado") == "Ajeno"]
        self.assertEqual(len(ajenos), 1)
        self.assertFalse(frappe.db.get_value("CFDI Recibido", ajenos[0], "archivo_pdf"),
                         "B/F1.pdf no es el PDF de A/F1.xml")

    def test_un_archivo_que_falla_a_medias_no_deja_basura(self):
        """Si la factura revienta DESPUÉS de insertarse, no queda la factura a medio hacer; el CFDI
        sí se queda registrado en estado Error para reintentarlo, y el resto del lote se procesa."""
        real = api.crear_factura_desde_cfdi
        llamadas = []

        def revienta_despues_de_insertar(cfdi_name):
            llamadas.append(cfdi_name)
            factura = real(cfdi_name)
            if len(llamadas) == 2:
                frappe.throw("fallo a propósito con la factura ya insertada")
            return factura

        api.crear_factura_desde_cfdi = revienta_despues_de_insertar
        try:
            r = procesar_archivos([subir("bueno.xml", ejemplos.INGRESO_40),
                                   subir("revienta.xml", ejemplos.INGRESO_33_RETENCIONES)])
        finally:
            api.crear_factura_desde_cfdi = real
        self.assertEqual(len(r["nuevos"]), 2)
        self.assertEqual(len(r["facturas"]), 1, "solo la factura del archivo bueno")
        self.assertEqual(len(r["errores"]), 1)
        malo = llamadas[1]
        self.assertEqual(frappe.db.get_value("CFDI Recibido", malo, "estado"), "Error")
        self.assertFalse(frappe.db.get_value("CFDI Recibido", malo, "factura"), "el CFDI no apunta a nada")
        self.assertFalse(frappe.db.exists("Purchase Invoice", {"cfdi_recibido": malo}),
                         "la factura a medio hacer se deshizo")
        self.assertEqual(frappe.db.count("Purchase Invoice", {"cfdi_recibido": ["!=", ""]}), 1)
