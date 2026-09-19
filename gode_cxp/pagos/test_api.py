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
