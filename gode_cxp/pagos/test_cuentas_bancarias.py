import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.proveedores import proveedor_por_rfc
from gode_cxp.facturas.pruebas_comun import usuario
from gode_cxp.pagos.cuentas_bancarias import (nombre_tef_para, naturaleza_por_clabe, transliterar, validar_clabe,
                                              validar_nombre_tef, verificar_cuenta)

# CLABEs con dígito verificador correcto: las dos de 18 dígitos que empiezan con 072 y 014 son las
# reales de los archivos de muestra que Banamex aceptó; la de Banamex (002) es sintética, con el
# dígito calculado con el mismo algoritmo de Banxico que implementa validar_clabe.
CLABE_BANAMEX = "002180012345678906"
CLABE_OTRO = "072180007090045065"       # del archivo PAGOS2.txt (Banorte)

REVISOR, TESORERIA = "prueba.revisor@cxp.local", "prueba.tesoreria@cxp.local"


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
        self.assertIsNone(validar_nombre_tef(largo))

    def test_nombre_tef_largo_conserva_la_estructura(self):
        """El recorte a 55 va sobre el cuerpo, nunca sobre la coma ni la diagonal: si se recortara el
        resultado ya armado, una razón social larga perdería la '/' final y el banco lo rechazaría."""
        moral = nombre_tef_para("Moral", "", "", "", "DISTRIBUIDORA DE ABARROTES Y CARNES SELECTAS DEL BAJIO SA DE CV")
        self.assertLessEqual(len(moral), 55)
        self.assertTrue(moral.endswith("/"), moral)
        self.assertTrue(moral.startswith("DISTRIBUIDORA,"), moral)
        self.assertIsNone(validar_nombre_tef(moral))
        # Física: se recorta materno, luego paterno, luego los nombres; la estructura queda entera.
        fisica = nombre_tef_para("Física", "María Guadalupe de los Ángeles", "Hernández", "de la Torre Montealegre", "")
        self.assertLessEqual(len(fisica), 55)
        self.assertTrue(fisica.startswith("MARIA GUADALUPE DE LOS ANGELES,HERNANDEZ/"), fisica)
        self.assertIsNone(validar_nombre_tef(fisica))

    def test_nombre_tef_fisica_sin_comas_ni_diagonales(self):
        """Una coma o una diagonal dentro de un apellido rompería la estructura: se quitan, como en la
        rama Moral."""
        s = nombre_tef_para("Física", "Ana, Luisa", "De la Cruz/Mora", "Ruiz", "")
        self.assertEqual(s, "ANA LUISA,DE LA CRUZ MORA/RUIZ")
        self.assertIsNone(validar_nombre_tef(s))

    def test_validar_nombre_tef(self):
        self.assertIsNone(validar_nombre_tef("AVICOLA,DEL CARMEN SA DE CV/"))
        # Lo que el banco tomó el 17/09/2026 tiene que ser válido aquí, o el histórico y la app dejan
        # de coincidir: un moral SIN coma, y un beneficiario con puntos capturado a mano.
        self.assertIsNone(validar_nombre_tef("MARINTER SA DE CV/"))
        self.assertIsNone(validar_nombre_tef("DISTRIBUIDORA,DE ALIMENTOS P.B. SA DE CV/"))
        self.assertIn("coma", validar_nombre_tef("DOS,COMAS,AQUI/"))
        self.assertIn("diagonal", validar_nombre_tef("SIN,DIAGONAL"))
        self.assertIn("diagonal", validar_nombre_tef("DOS,DIAGONALES//"))
        # Con "$" en vez de re.fullmatch este salto de línea pasaría y correría el archivo entero.
        self.assertIsNotNone(validar_nombre_tef("ABC,DEF/\n"))
        self.assertIsNotNone(validar_nombre_tef("ÑU,AND/"))
        self.assertIsNotNone(validar_nombre_tef(""))
        self.assertIsNotNone(validar_nombre_tef("A" * 55 + "/"))


class TestCuentaBancaria(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()

    def setUp(self):
        frappe.set_user("Administrator")
        # limpiar() borra también las cuentas bancarias de los proveedores de prueba: el name de
        # Bank Account es account_name + " - " + banco, así que sin eso la segunda prueba de la
        # clase chocaría con la cuenta que dejó la primera.
        pruebas_comun.limpiar()
        self.proveedor = proveedor_por_rfc("AVI900101AB1", "Avícola del Carmen SA de CV")

    def tearDown(self):
        frappe.set_user("Administrator")

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

    def test_borrar_la_clabe_limpia_los_datos_del_banco(self):
        """Sin CLABE no hay TEF: dejar la sucursal y la cuenta Banamex de la CLABE anterior haría que
        un lote futuro armara un registro con datos de una cuenta que ya no existe."""
        c = self._cuenta(bank="Banamex", clabe=CLABE_BANAMEX, sucursal_banamex="7005", cuenta_banamex="7479513")
        c.clabe = ""
        c.save(ignore_permissions=True)
        self.assertFalse(c.tipo_pago_tef)
        self.assertFalse(c.sucursal_banamex)
        self.assertFalse(c.cuenta_banamex)

    def test_clabe_invalida(self):
        with self.assertRaises(frappe.ValidationError):
            self._cuenta(clabe="072180007090045066")

    def test_nombre_invalido(self):
        with self.assertRaises(frappe.ValidationError):
            self._cuenta(clabe=CLABE_OTRO, nombre_tef="AVICOLA DEL CARMEN SA DE CV")   # sin diagonal

    def test_fisica_sin_apellidos_pide_capturarlos(self):
        """Con un proveedor físico sin nombre ni apellidos no se puede armar NOMBRES,PATERNO/MATERNO:
        hay que pedirlos, no adivinar con el formato de una moral."""
        fisico = proveedor_por_rfc("HESB850101AB1", "Bruno Hernández Silva")
        with self.assertRaises(frappe.ValidationError) as ctx:
            self._cuenta(account_name="Bruno prueba", party=fisico, clabe=CLABE_OTRO)
        self.assertIn("apellidos", str(ctx.exception))

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

    def test_repuntar_la_cuenta_a_otro_proveedor_pierde_la_verificacion(self):
        """El beneficiario y la CLABE siguen iguales, pero el dinero iría a otro proveedor: lo que
        verificó Tesorería ya no es lo que se va a mandar al banco."""
        c = self._cuenta(clabe=CLABE_OTRO)
        verificar_cuenta(c.name)
        c.reload()
        self.assertEqual(c.verificada, 1)
        c.party = proveedor_por_rfc("AVI900101AB2", "Avícola Segunda SA de CV")
        c.save(ignore_permissions=True)
        self.assertEqual(c.verificada, 0)

    def test_solo_tesoreria_puede_marcar_verificada_a_mano(self):
        """`verificada` es read_only en pantalla, pero eso no frena un save() por API o por consola."""
        c = self._cuenta(clabe=CLABE_OTRO)
        usuario(REVISOR, "CxP Revisor")
        usuario(TESORERIA, "CxP Tesoreria")
        frappe.set_user(REVISOR)
        c.verificada = 1
        # ignore_permissions a propósito: así el que se queja es el candado y no el permiso de
        # DocType (CxP Revisor sólo lee Bank Account). Lo que se prueba es la regla de negocio.
        with self.assertRaises(frappe.PermissionError) as ctx:
            c.save(ignore_permissions=True)
        self.assertIn("Tesorería", str(ctx.exception))
        frappe.set_user(TESORERIA)
        c.reload()
        c.verificada = 1
        c.save(ignore_permissions=True)
        self.assertEqual(c.verificada, 1)

    def test_verificar_dos_veces_no_reescribe_el_sello(self):
        c = self._cuenta(clabe=CLABE_OTRO)
        verificar_cuenta(c.name)
        # Se cambia el sello a mano para que se note si la segunda llamada lo reescribe.
        otro = usuario(TESORERIA, "CxP Tesoreria")
        frappe.db.set_value("Bank Account", c.name, "verificada_por", otro, update_modified=False)
        verificar_cuenta(c.name)
        self.assertEqual(frappe.db.get_value("Bank Account", c.name, "verificada_por"), otro)

    def test_no_se_verifica_la_cuenta_de_un_proveedor_bloqueado(self):
        c = self._cuenta(clabe=CLABE_OTRO)
        frappe.db.set_value("Supplier", self.proveedor, "bloqueado_pagos", 1)
        with self.assertRaises(frappe.ValidationError):
            verificar_cuenta(c.name)
        self.assertEqual(frappe.db.get_value("Bank Account", c.name, "verificada"), 0)
