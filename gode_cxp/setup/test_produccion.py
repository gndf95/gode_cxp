"""configurar_empresa: lo que dice que hará (dry-run) y que aplicarlo dos veces no cambia nada."""
import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import EMPRESA
from gode_cxp.setup.produccion import CUENTAS, ITEM_GENERICO, NADA_QUE_HACER, configurar_empresa

RFC = "GES200101ABC"
FRASE_POR_PAGAR = "Cambiar la cuenta por pagar por defecto"


def una_cuenta(**filtros):
    """Una cuenta de detalle de la empresa que cumpla los filtros (la primera por `lft`)."""
    return frappe.db.get_value("Account", dict({"company": EMPRESA, "is_group": 0}, **filtros), "name", order_by="lft")


def cuentas_por_pagar_validas():
    """Las cuentas que sirven como cuenta por pagar por defecto: Payable, de detalle y en la moneda
    de la empresa. Son las que el script NO debe pisar si la empresa ya apunta a una de ellas."""
    moneda = frappe.db.get_value("Company", EMPRESA, "default_currency")
    return [c.name for c in frappe.get_all("Account",
                                           filters={"company": EMPRESA, "account_type": "Payable", "is_group": 0},
                                           fields=["name", "account_currency"], order_by="lft")
            if not c.account_currency or c.account_currency == moneda]


def olvidar_las_cuentas():
    """Deja la Configuración CxP sin cuentas, borra las que crea el script y deja la cuenta por
    pagar de la empresa apuntando a una que NO sirve, para poder recorrer el camino completo: el
    que correrá en producción la primera vez."""
    for campo in CUENTAS:
        frappe.db.set_single_value("Configuracion CxP", campo, None)
    for receta in CUENTAS.values():
        nombre = frappe.db.get_value("Account", {"company": EMPRESA, "account_name": receta["nombre"]}, "name")
        if nombre:
            frappe.delete_doc("Account", nombre, force=1, ignore_permissions=True)
    # set_value y no el doc: se quiere dejar a la empresa en el estado inválido a propósito.
    frappe.db.set_value("Company", EMPRESA, "default_payable_account", una_cuenta(root_type="Expense"))
    frappe.db.commit()


class TestProduccion(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Sin facturas de prueba vivas se pueden borrar y volver a crear las cuentas.
        pruebas_comun.preparar_sitio_pruebas()   # limpiar() exige que la config apunte a GODE PRUEBAS
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
        # El resumen va aparte: en `acciones` sólo hay cosas por hacer.
        self.assertNotIn(NADA_QUE_HACER, r["acciones"])
        self.assertIn("no se escribió nada", r["resumen"])
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
        self.assertEqual(r2["resumen"], NADA_QUE_HACER)
        conf = frappe.get_doc("Configuracion CxP")
        self.assertEqual((conf.empresa, conf.rfc_empresa, conf.item_generico), (EMPRESA, RFC, ITEM_GENERICO))
        for campo in CUENTAS:
            self.assertTrue(conf.get(campo), campo)
            self.assertEqual(frappe.db.get_value("Account", conf.get(campo), "company"), EMPRESA)
        pagar = frappe.db.get_value("Company", EMPRESA, "default_payable_account")
        self.assertEqual(frappe.db.get_value("Account", pagar, "account_type"), "Payable")
        self.assertEqual(frappe.db.get_value("Account", pagar, "is_group"), 0)

    def test_la_cuenta_por_pagar_invalida_se_reporta_y_se_corrige(self):
        """La rama que hace Company.save(): la empresa apunta a una cuenta que no es Payable."""
        olvidar_las_cuentas()
        rota = frappe.db.get_value("Company", EMPRESA, "default_payable_account")
        self.assertTrue(rota)
        self.assertNotEqual(frappe.db.get_value("Account", rota, "account_type"), "Payable")
        frases = [a for a in configurar_empresa(EMPRESA, RFC, dry_run=True)["acciones"]
                  if a.startswith(FRASE_POR_PAGAR)]
        self.assertEqual(len(frases), 1, frases)
        self.assertIn(rota, frases[0])
        # El dry-run lo dijo pero no lo hizo.
        self.assertEqual(frappe.db.get_value("Company", EMPRESA, "default_payable_account"), rota)
        configurar_empresa(EMPRESA, RFC, dry_run=False)
        ahora = frappe.db.get_value("Company", EMPRESA, "default_payable_account")
        self.assertNotEqual(ahora, rota)
        self.assertEqual(frappe.db.get_value("Account", ahora, "company"), EMPRESA)
        self.assertEqual(frappe.db.get_value("Account", ahora, "account_type"), "Payable")
        self.assertEqual(frappe.db.get_value("Account", ahora, "is_group"), 0)

    def test_no_pisa_una_cuenta_por_pagar_que_ya_sirve(self):
        """En producción esa cuenta está puesta a propósito: si sirve, ni se menciona."""
        validas = cuentas_por_pagar_validas()
        self.assertTrue(validas, "el sitio de pruebas debería tener alguna cuenta Payable")
        elegida = validas[-1]      # a propósito la última, para que no sea la que el script prefiere
        antes = frappe.db.get_value("Company", EMPRESA, "default_payable_account")
        frappe.db.set_value("Company", EMPRESA, "default_payable_account", elegida)
        frappe.db.commit()
        try:
            r = configurar_empresa(EMPRESA, RFC, dry_run=False)
            self.assertFalse([a for a in r["acciones"] if a.startswith(FRASE_POR_PAGAR)], r["acciones"])
            self.assertEqual(frappe.db.get_value("Company", EMPRESA, "default_payable_account"), elegida)
        finally:
            frappe.db.set_value("Company", EMPRESA, "default_payable_account", antes)
            frappe.db.commit()

    def test_las_cuentas_nuevas_quedan_del_tipo_correcto(self):
        olvidar_las_cuentas()
        configurar_empresa(EMPRESA, RFC, dry_run=False)
        conf = frappe.get_doc("Configuracion CxP")
        for campo, receta in CUENTAS.items():
            cuenta = frappe.get_doc("Account", conf.get(campo))
            self.assertEqual(cuenta.root_type, receta["root_type"], campo)
            self.assertEqual(cuenta.is_group, 0, campo)
            self.assertTrue(cuenta.parent_account, campo)
            # El account_type sólo se garantiza en las que crea el script: si reaprovecha una del
            # catálogo, la deja como está y lo avisa en el reporte.
            if receta["account_type"] and cuenta.account_name == receta["nombre"]:
                self.assertEqual(cuenta.account_type, receta["account_type"], campo)

    def test_se_puede_decir_a_mano_que_cuenta_usar(self):
        """En producción puede convenir apuntar a una cuenta que ya existe en vez de crear otra."""
        olvidar_las_cuentas()
        a_mano = una_cuenta(root_type="Expense")
        r = configurar_empresa(EMPRESA, RFC, dry_run=False, cuentas={"cuenta_gasto_default": a_mano})
        self.assertEqual(frappe.db.get_single_value("Configuracion CxP", "cuenta_gasto_default"), a_mano)
        self.assertFalse([a for a in r["acciones"] if a.startswith("Crear la cuenta 'Compras y gastos CFDI")], r["acciones"])
        # Esa cuenta elegida a mano no debe sobrevivir a la prueba: el resto de la suite espera la normal.
        olvidar_las_cuentas()

    def test_se_puede_decir_a_mano_la_cuenta_por_pagar(self):
        olvidar_las_cuentas()
        elegida = cuentas_por_pagar_validas()[-1]
        antes = frappe.db.get_value("Company", EMPRESA, "default_payable_account")
        try:
            r = configurar_empresa(EMPRESA, RFC, dry_run=False,
                                   cuentas={"default_payable_account": elegida})
            self.assertTrue([a for a in r["acciones"] if a.startswith(FRASE_POR_PAGAR)], r["acciones"])
            self.assertEqual(frappe.db.get_value("Company", EMPRESA, "default_payable_account"), elegida)
        finally:
            frappe.db.set_value("Company", EMPRESA, "default_payable_account", antes)
            frappe.db.commit()

    def test_avisa_si_la_cuenta_a_mano_no_es_del_tipo_esperado(self):
        """El aviso REVISAR sale siempre: en dry-run y también cuando ya no hay nada que cambiar."""
        olvidar_las_cuentas()
        gasto = una_cuenta(root_type="Expense")       # se pide como IVA acreditable, que es Asset
        a_mano = {"cuenta_iva_acreditable": gasto}
        r = configurar_empresa(EMPRESA, RFC, dry_run=True, cuentas=a_mano)
        self.assertTrue([a for a in r["acciones"] if a.startswith("REVISAR:") and gasto in a], r["acciones"])
        configurar_empresa(EMPRESA, RFC, dry_run=False, cuentas=a_mano)
        r2 = configurar_empresa(EMPRESA, RFC, dry_run=False, cuentas=a_mano)
        self.assertTrue([a for a in r2["acciones"] if a.startswith("REVISAR:")], r2["acciones"])
        olvidar_las_cuentas()   # que la cuenta equivocada no sobreviva a la prueba

    def test_una_clave_desconocida_en_cuentas_no_pasa(self):
        with self.assertRaises(frappe.ValidationError):
            configurar_empresa(EMPRESA, RFC, dry_run=True, cuentas={"cuenta_del_iva": una_cuenta()})

    def test_una_cuenta_de_grupo_no_pasa(self):
        grupo = frappe.db.get_value("Account", {"company": EMPRESA, "is_group": 1, "root_type": "Expense"},
                                    "name", order_by="lft")
        with self.assertRaises(frappe.ValidationError):
            configurar_empresa(EMPRESA, RFC, dry_run=True, cuentas={"cuenta_gasto_default": grupo})

    def test_una_cuenta_que_no_existe_no_pasa(self):
        with self.assertRaises(frappe.ValidationError):
            configurar_empresa(EMPRESA, RFC, dry_run=True,
                               cuentas={"cuenta_gasto_default": "CUENTA QUE NO EXISTE - ZZ"})

    def test_la_empresa_tiene_que_existir(self):
        with self.assertRaises(frappe.ValidationError):
            configurar_empresa("EMPRESA QUE NO EXISTE", RFC, dry_run=True)
