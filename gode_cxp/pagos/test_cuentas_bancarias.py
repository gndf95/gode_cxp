import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.proveedores import proveedor_por_rfc
from gode_cxp.pagos.cuentas_bancarias import (nombre_tef_para, naturaleza_por_clabe, transliterar, validar_clabe,
                                              validar_nombre_tef, verificar_cuenta)

# CLABEs con dígito verificador correcto: las dos de 18 dígitos que empiezan con 072 y 014 son las
# reales de los archivos de muestra que Banamex aceptó; la de Banamex (002) es sintética, con el
# dígito calculado con el mismo algoritmo de Banxico que implementa validar_clabe.
CLABE_BANAMEX = "002180012345678906"
CLABE_OTRO = "072180007090045065"       # del archivo PAGOS2.txt (Banorte)


class TestPuras(FrappeTestCase):
    def test_clabe(self):
        self.assertTrue(validar_clabe("072180007090045065"))
        self.assertTrue(validar_clabe("014180655090628465"))
        self.assertTrue(validar_clabe(CLABE_BANAMEX))
        self.assertFalse(validar_clabe("072180007090045066"))   # dígito cambiado
        self.assertFalse(validar_clabe("07218000709004506"))    # 17 dígitos
        self.assertFalse(validar_clabe("07218000709004506A"))

    def test_naturaleza(self):
        self.assertEqual(naturaleza_por_clabe(CLABE_BANAMEX), "06")
        self.assertEqual(naturaleza_por_clabe(CLABE_OTRO), "12")

    def test_transliterar(self):
        self.assertEqual(transliterar("Distribuidora de Alimentos P.B., S.A. de C.V."), "DISTRIBUIDORA DE ALIMENTOS PB, SA DE CV")
        self.assertEqual(transliterar("Núñez  Peña / Hnos."), "NUNEZ PENA / HNOS")

    def test_nombre_tef(self):
        self.assertEqual(nombre_tef_para("Moral", "", "", "", "AVICOLA DEL CARMEN SA DE CV"), "AVICOLA,DEL CARMEN SA DE CV/")
        self.assertEqual(nombre_tef_para("Moral", "", "", "", "Trece Siete Group, S.A. de C.V."), "TRECE,SIETE GROUP SA DE CV/")
        self.assertEqual(nombre_tef_para("Física", "Sonia", "Cruz", "Ruiz", "SONIA CRUZ RUIZ"), "SONIA,CRUZ/RUIZ")
        self.assertEqual(nombre_tef_para("Física", "Bruno Ricardo", "Hernández", "Silva", ""), "BRUNO RICARDO,HERNANDEZ/SILVA")
        largo = nombre_tef_para("Moral", "", "", "", "A" * 70)
        self.assertEqual(len(largo), 55)

    def test_validar_nombre_tef(self):
        self.assertIsNone(validar_nombre_tef("AVICOLA,DEL CARMEN SA DE CV/"))
        self.assertIn("punto", validar_nombre_tef("DISTRIBUIDORA P.B.,SA/"))
        self.assertIn("coma", validar_nombre_tef("SIN COMA/"))
        self.assertIn("diagonal", validar_nombre_tef("SIN,DIAGONAL"))
        self.assertIsNotNone(validar_nombre_tef("ÑU,AND/"))


class TestCuentaBancaria(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()

    def setUp(self):
        # limpiar() borra también las cuentas bancarias de los proveedores de prueba: el name de
        # Bank Account es account_name + " - " + banco, así que sin eso la segunda prueba de la
        # clase chocaría con la cuenta que dejó la primera.
        pruebas_comun.limpiar()
        self.proveedor = proveedor_por_rfc("AVI900101AB1", "Avícola del Carmen SA de CV")

    def _cuenta(self, **campos):
        datos = {"doctype": "Bank Account", "account_name": "Avícola prueba", "bank": "Banorte", "party_type": "Supplier", "party": self.proveedor}
        datos.update(campos)
        if not frappe.db.exists("Bank", datos["bank"]):
            frappe.get_doc({"doctype": "Bank", "bank_name": datos["bank"]}).insert(ignore_permissions=True)
        doc = frappe.get_doc(datos)
        doc.insert(ignore_permissions=True)
        return doc

    def test_clabe_llena_naturaleza_y_nombre(self):
        c = self._cuenta(clabe=CLABE_OTRO)
        self.assertEqual(c.tipo_pago_tef, "12")
        self.assertEqual(c.nombre_tef, "AVICOLA,DEL CARMEN SA DE CV/")
        self.assertEqual(c.verificada, 0)

    def test_clabe_banamex_exige_sucursal_y_cuenta(self):
        with self.assertRaises(frappe.ValidationError):
            self._cuenta(bank="Banamex", clabe=CLABE_BANAMEX)
        c = self._cuenta(bank="Banamex", clabe=CLABE_BANAMEX, sucursal_banamex="7005", cuenta_banamex="7479513")
        self.assertEqual(c.tipo_pago_tef, "06")

    def test_clabe_invalida(self):
        with self.assertRaises(frappe.ValidationError):
            self._cuenta(clabe="072180007090045066")

    def test_nombre_invalido(self):
        with self.assertRaises(frappe.ValidationError):
            self._cuenta(clabe=CLABE_OTRO, nombre_tef="AVICOLA P.B.,SA/")

    def test_verificar_y_perder_verificacion(self):
        c = self._cuenta(clabe=CLABE_OTRO)
        verificar_cuenta(c.name)
        c.reload()
        self.assertEqual((c.verificada, c.verificada_por), (1, frappe.session.user))
        self.assertTrue(c.verificada_el)
        c.clabe = "014180655090628465"
        c.save(ignore_permissions=True)
        self.assertEqual(c.verificada, 0)
        self.assertEqual(c.tipo_pago_tef, "12")
