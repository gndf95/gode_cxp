"""configurar_empresa: lo que dice que hará (dry-run) y que aplicarlo dos veces no cambia nada."""
import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import EMPRESA
from gode_cxp.setup.produccion import CUENTAS, ITEM_GENERICO, configurar_empresa

RFC = "GES200101ABC"


def olvidar_las_cuentas():
    """Deja la Configuración CxP sin cuentas y borra las que crea el script, para poder recorrer el
    camino completo: el que correrá en producción la primera vez."""
    for campo in CUENTAS:
        frappe.db.set_single_value("Configuracion CxP", campo, None)
    for receta in CUENTAS.values():
        nombre = frappe.db.get_value("Account", {"company": EMPRESA, "account_name": receta["nombre"]}, "name")
        if nombre:
            frappe.delete_doc("Account", nombre, force=1, ignore_permissions=True)
    frappe.db.commit()


class TestProduccion(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Sin facturas de prueba vivas se pueden borrar y volver a crear las cuentas.
        pruebas_comun.limpiar()

    def setUp(self):
        pruebas_comun.preparar_sitio_pruebas()

    @classmethod
    def tearDownClass(cls):
        pruebas_comun.preparar_sitio_pruebas()   # el sitio queda como lo esperan las demás pruebas
        super().tearDownClass()

    def test_dry_run_no_cambia_nada(self):
        olvidar_las_cuentas()   # así el dry-run sí tiene cosas que reportar
        antes_rfc = frappe.db.get_single_value("Configuracion CxP", "rfc_empresa")
        antes_cuentas = frappe.db.count("Account", {"company": EMPRESA})
        antes_pagar = frappe.db.get_value("Company", EMPRESA, "default_payable_account")
        r = configurar_empresa(EMPRESA, "XAXX010101000", dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertIn("acciones", r)
        self.assertTrue(r["acciones"], "el dry-run debería tener algo que reportar")
        # El reporte lo lee una persona: son frases sueltas en español, no estructuras.
        self.assertTrue(all(isinstance(a, str) for a in r["acciones"]))
        self.assertTrue(any(a.startswith("Crear la cuenta") for a in r["acciones"]), r["acciones"])
        # Y no escribió nada: ni cuentas, ni la configuración, ni la cuenta por pagar de la empresa.
        self.assertEqual(frappe.db.count("Account", {"company": EMPRESA}), antes_cuentas)
        self.assertEqual(frappe.db.get_single_value("Configuracion CxP", "rfc_empresa"), antes_rfc)
        self.assertEqual(frappe.db.get_value("Company", EMPRESA, "default_payable_account"), antes_pagar)
        for campo in CUENTAS:
            self.assertFalse(frappe.db.get_single_value("Configuracion CxP", campo), campo)

    def test_aplicar_es_idempotente(self):
        olvidar_las_cuentas()
        r1 = configurar_empresa(EMPRESA, RFC, dry_run=False)
        self.assertTrue(r1["acciones"], "la primera vez sí hay trabajo que hacer")
        r2 = configurar_empresa(EMPRESA, RFC, dry_run=False)
        self.assertEqual(r2["acciones"], [])            # segunda vez: nada que hacer
        conf = frappe.get_doc("Configuracion CxP")
        self.assertEqual((conf.empresa, conf.rfc_empresa, conf.item_generico), (EMPRESA, RFC, ITEM_GENERICO))
        for campo in CUENTAS:
            self.assertTrue(conf.get(campo), campo)
            self.assertEqual(frappe.db.get_value("Account", conf.get(campo), "company"), EMPRESA)
        pagar = frappe.db.get_value("Company", EMPRESA, "default_payable_account")
        self.assertEqual(frappe.db.get_value("Account", pagar, "account_type"), "Payable")
        self.assertEqual(frappe.db.get_value("Account", pagar, "is_group"), 0)

    def test_las_cuentas_nuevas_quedan_del_tipo_correcto(self):
        olvidar_las_cuentas()
        configurar_empresa(EMPRESA, RFC, dry_run=False)
        conf = frappe.get_doc("Configuracion CxP")
        for campo, receta in CUENTAS.items():
            cuenta = frappe.get_doc("Account", conf.get(campo))
            self.assertEqual(cuenta.root_type, receta["root_type"], campo)
            self.assertEqual(cuenta.is_group, 0, campo)
            self.assertTrue(cuenta.parent_account, campo)
            if receta["account_type"]:
                self.assertEqual(cuenta.account_type, receta["account_type"], campo)

    def test_se_puede_decir_a_mano_que_cuenta_usar(self):
        """En producción puede convenir apuntar a una cuenta que ya existe en vez de crear otra."""
        olvidar_las_cuentas()
        a_mano = frappe.db.get_value("Account", {"company": EMPRESA, "root_type": "Expense", "is_group": 0}, "name", order_by="lft")
        r = configurar_empresa(EMPRESA, RFC, dry_run=False, cuentas={"cuenta_gasto_default": a_mano})
        self.assertEqual(frappe.db.get_single_value("Configuracion CxP", "cuenta_gasto_default"), a_mano)
        self.assertFalse([a for a in r["acciones"] if a.startswith("Crear la cuenta 'Compras y gastos CFDI")], r["acciones"])

    def test_la_empresa_tiene_que_existir(self):
        with self.assertRaises(frappe.ValidationError):
            configurar_empresa("EMPRESA QUE NO EXISTE", RFC, dry_run=True)
