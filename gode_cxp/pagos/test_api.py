"""Los puntos de entrada del escritorio para los lotes: quién puede llamarlos y qué devuelven.

Aquí no se vuelve a probar la lógica de los lotes (eso es test_lotes.py): se prueba el contrato que
consumen los botones del escritorio —permiso por rol y la forma del valor de vuelta— porque el JS no
se puede correr en las pruebas.
"""
import json
from datetime import date

import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import cuenta_verificada, factura_aprobada, usuario
from gode_cxp.pagos import api
from gode_cxp.setup.produccion import configurar_pagos

TESORERIA, REVISOR = "prueba.tesoreria@cxp.local", "prueba.revisor@cxp.local"
# Alguien de contabilidad ajeno a CxP: ni siquiera puede consultar qué se puede pagar.
AJENO = "prueba.contable@cxp.local"
CLABE_12 = "072180007090045065"
# Un valor que el usuario SÍ puede ver, distinto del que la prueba va a tocar: es lo que convierte
# una User Permission en una negación para todo lo demás.
AJENA = "EMPRESA AJENA"


class TestApiPagos(FrappeTestCase):
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
        # limpiar() borra los usuarios de prueba, así que se recrean antes de cada prueba.
        usuario(TESORERIA, "CxP Tesoreria"); usuario(REVISOR, "CxP Revisor"); usuario(AJENO, "Accounts User")
        self.fa = factura_aprobada(ejemplos.INGRESO_40)
        cuenta_verificada(self.fa.supplier, CLABE_12)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _partidas(self, importe=100):
        return json.dumps([{"factura": self.fa.name, "importe": importe}])

    def _respuesta_limpia(self):
        """`descargar_archivo` escribe en `frappe.local.response`, que es global de la petición: en
        las pruebas hay una sola, así que se restaura al terminar para no ensuciar a las demás."""
        antes = dict(frappe.local.response)

        def restaurar():
            frappe.local.response.clear()
            frappe.local.response.update(antes)
        self.addCleanup(restaurar)

    def _lote_exportado(self):
        """Un lote autorizado y con su archivo TEF ya generado."""
        (lote,) = api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())
        frappe.get_doc("Lote de Pago", lote).submit()
        return lote, api.generar_archivo(lote)

    def _solo_ve(self, doctype, valor):
        """Le deja al usuario de Tesorería un único documento permitido de `doctype`, o sea le niega
        todos los demás. Es la forma real de ejercitar las User Permissions por empresa sin dar de
        alta otro catálogo de cuentas en el sitio de pruebas.

        `ignore_links`: a User Permission no le importa que `valor` exista (sólo guarda la lista de
        valores permitidos) y aquí lo que se quiere es justamente que NO sea el de la prueba."""
        up = frappe.get_doc({"doctype": "User Permission", "user": TESORERIA, "allow": doctype,
                             "for_value": valor})
        up.flags.ignore_links = True
        up.insert(ignore_permissions=True)
        # LIFO: primero se borra la User Permission y después se limpia la caché del usuario.
        self.addCleanup(frappe.clear_cache, user=TESORERIA)
        self.addCleanup(frappe.delete_doc, "User Permission", up.name, force=1, ignore_permissions=True)
        frappe.clear_cache(user=TESORERIA)

    def test_el_permiso_sobre_el_lote_tambien_cuenta(self):
        """El rol no alcanza: si las User Permissions de la empresa le niegan el lote a quien llama,
        los tres botones que mueven dinero tienen que negarse igual. `_exigir` sólo mira el rol, que
        es global; el permiso por documento es lo que separa una empresa de otra."""
        (lote,) = api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())
        frappe.get_doc("Lote de Pago", lote).submit()
        api.generar_archivo(lote)                 # queda Exportado: sin el candado, marcar_transmitido pasaría
        self._solo_ve("Lote de Pago", "LOTE-QUE-NO-ES-ESTE")
        frappe.set_user(TESORERIA)
        for llamada in (lambda: api.generar_archivo(lote),
                        lambda: api.marcar_transmitido(lote, "119938"),
                        lambda: api.nuevo_lote_pendientes(lote)):
            with self.assertRaises(frappe.PermissionError):
                llamada()
        self.assertEqual(frappe.db.get_value("Lote de Pago", lote, "estado_lote"), "Exportado")

    def test_facturas_pagables_exige_permiso_de_la_empresa(self):
        """Consultar qué se puede pagar es una consulta de facturas de una empresa: si el usuario no
        tiene permitida esa empresa, no puede verlas. `lotes.facturas_pagables` usa `frappe.get_all`,
        que va con `ignore_permissions=True`, así que el candado tiene que estar en la API."""
        frappe.set_user(TESORERIA)
        self.assertEqual([f["name"] for f in api.facturas_pagables(pruebas_comun.EMPRESA)], [self.fa.name])
        frappe.set_user("Administrator")
        self._solo_ve("Company", AJENA)
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.PermissionError):
            api.facturas_pagables(pruebas_comun.EMPRESA)

    def test_administrator_pasa_los_candados(self):
        """'Administrator' no tiene filas en Has Role, pero `frappe.get_roles` le devuelve todos los
        roles del sitio: los candados de la API no pueden dejar fuera al administrador (es quien
        corre las migraciones y quien arregla un lote a mano)."""
        api._exigir("CxP Tesoreria")           # no lanza
        self.assertEqual([f["name"] for f in api.facturas_pagables(pruebas_comun.EMPRESA)], [self.fa.name])
        (lote,) = api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())
        frappe.get_doc("Lote de Pago", lote).submit()
        self.assertTrue(api.generar_archivo(lote)["file_url"])
        self.assertEqual(api.marcar_transmitido(lote, "119938"), lote)

    def test_revisor_consulta_pero_no_crea(self):
        frappe.set_user(REVISOR)
        self.assertEqual([f["name"] for f in api.facturas_pagables(pruebas_comun.EMPRESA)], [self.fa.name])
        with self.assertRaises(frappe.PermissionError):
            api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())

    def test_ajeno_a_cxp_no_consulta(self):
        frappe.set_user(AJENO)
        with self.assertRaises(frappe.PermissionError):
            api.facturas_pagables(pruebas_comun.EMPRESA)

    def test_solo_tesoreria_mueve_el_lote(self):
        """Los cuatro botones del formulario del lote son de Tesorería, no del revisor."""
        frappe.set_user(TESORERIA)
        (lote,) = api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())
        frappe.set_user(REVISOR)
        for llamada in (lambda: api.generar_archivo(lote),
                        lambda: api.marcar_transmitido(lote, "119938"),
                        lambda: api.nuevo_lote_pendientes(lote)):
            with self.assertRaises(frappe.PermissionError):
                llamada()

    def test_tesoreria_crea_desde_json(self):
        frappe.set_user(TESORERIA)
        lotes = api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())
        self.assertEqual(len(lotes), 1)
        self.assertEqual(frappe.db.get_value("Lote de Pago", lotes[0], "owner"), TESORERIA)

    def test_tesoreria_genera_y_transmite(self):
        """Lo que devuelven los botones: 'Generar archivo TEF' abre r.message.file_url y
        'Marcar transmitido' recarga el lote que le contesta la llamada."""
        frappe.set_user(TESORERIA)
        (lote,) = api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())
        frappe.get_doc("Lote de Pago", lote).submit()
        respuesta = api.generar_archivo(lote)
        self.assertTrue(respuesta["file_url"])
        self.assertTrue(respuesta["nombre_archivo"].endswith("-12.txt"))
        self.assertEqual(api.marcar_transmitido(lote, "119938"), lote)
        self.assertEqual(frappe.db.get_value("Lote de Pago", lote, "estado_lote"), "Transmitido")

    # ------------------------------------------------- descarga del archivo TEF

    def test_descargar_el_archivo_lo_baja_en_vez_de_abrirlo(self):
        """El `file_url` privado se lo sirve Frappe al navegador como texto plano y BancaNet
        necesita el .txt en disco. `frappe.response.type = "download"` es lo que hace que la
        respuesta salga con Content-Disposition: attachment (frappe/utils/response.py::as_raw)."""
        frappe.set_user(TESORERIA)
        lote, generado = self._lote_exportado()
        self._respuesta_limpia()
        api.descargar_archivo(lote)
        self.assertEqual(frappe.local.response["type"], "download")
        self.assertEqual(frappe.local.response["filename"], generado["nombre_archivo"])
        # los mismos bytes del File adjunto, sin recodificar: el archivo va en ancho fijo con CRLF
        # (se leen del disco y no con File.get_content(), que devuelve `str` para un .txt)
        adjunto = frappe.get_doc("File", frappe.db.get_value("File", {"file_url": generado["file_url"]}, "name"))
        with open(adjunto.get_full_path(), "rb") as f:
            esperado = f.read()
        contenido = frappe.local.response["filecontent"]
        self.assertIsInstance(contenido, bytes)
        self.assertEqual(contenido, esperado)
        self.assertTrue(contenido.endswith(b"\r\n"), contenido[-4:])

    def test_descargar_el_archivo_respeta_el_permiso_sobre_el_lote(self):
        """Igual que los botones que mueven dinero: el rol es global, el archivo lleva los datos
        bancarios de los proveedores, y las User Permissions sólo se aplican mirando el documento."""
        lote, _ = self._lote_exportado()
        self._solo_ve("Lote de Pago", "LOTE-QUE-NO-ES-ESTE")
        self._respuesta_limpia()
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.PermissionError):
            api.descargar_archivo(lote)
        frappe.set_user(AJENO)
        with self.assertRaises(frappe.PermissionError):
            api.descargar_archivo(lote)

    def test_descargar_un_lote_sin_archivo_lo_dice_en_espanol(self):
        frappe.set_user(TESORERIA)
        (lote,) = api.crear_lotes(pruebas_comun.EMPRESA, str(date.today()), self._partidas())
        self._respuesta_limpia()
        with self.assertRaises(frappe.ValidationError):
            api.descargar_archivo(lote)
        self.assertNotIn("filecontent", frappe.local.response)
