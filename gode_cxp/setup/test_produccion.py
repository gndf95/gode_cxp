"""configurar_empresa: lo que dice que hará (dry-run) y que aplicarlo dos veces no cambia nada."""
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import EMPRESA
from gode_cxp.setup.produccion import (CUENTAS, ITEM_GENERICO, NADA_QUE_HACER, _elegir_cuenta_por_pagar,
                                       configurar_empresa)

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
    """Deja la Configuración CxP sin cuentas y la cuenta por pagar de la empresa apuntando a una que
    NO sirve, para recorrer el camino de una empresa sin configurar.

    Las cuentas que el script crea se borran si se puede: en cuanto una tiene asientos, ERPNext ya
    no deja borrarla (y en el sitio de pruebas quedan asientos cancelados huérfanos de las facturas
    que borra `limpiar()`). Cuando no se puede, el script las reusa, que es el otro camino bueno; el
    de crearlas se prueba aparte con un nombre que nadie más usa."""
    for campo in CUENTAS:
        frappe.db.set_single_value("Configuracion CxP", campo, None)
    for receta in CUENTAS.values():
        nombre = frappe.db.get_value("Account", {"company": EMPRESA, "account_name": receta["nombre"]}, "name")
        # Mismo criterio que Account.on_trash de ERPNext: cualquier GL Entry, cancelada o no.
        if nombre and not frappe.db.exists("GL Entry", {"account": nombre}):
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
        self.assertTrue([a for a in r["acciones"]
                         if a.startswith("Crear la cuenta") or a.startswith("Reusar la cuenta")], r["acciones"])
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
        """En producción esa cuenta está puesta a propósito: si sirve, ni se menciona. La prueba no
        es vacua: 'elegida' tiene que ser distinta de la que el script hubiera preferido, si no,
        no se distingue 'no la tocó' de 'la puso de casualidad'."""
        empresa = frappe.get_doc("Company", EMPRESA)
        preferida = _elegir_cuenta_por_pagar(empresa)
        otras = [c for c in cuentas_por_pagar_validas() if c != preferida]
        creada = None
        if otras:
            elegida = otras[-1]
        else:
            # Sólo hay una Payable válida (la preferida): se crea una segunda desechable, bajo el
            # mismo padre, para poder probar que el script no pisa una que ya sirve aunque no sea
            # la que él mismo elegiría.
            padre = frappe.db.get_value("Account", preferida, "parent_account")
            elegida = creada = frappe.get_doc({
                "doctype": "Account", "account_name": "Por pagar de prueba desechable",
                "parent_account": padre, "company": EMPRESA, "root_type": "Liability",
                "is_group": 0, "account_type": "Payable"}).insert(ignore_permissions=True).name
        self.assertNotEqual(elegida, preferida)
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
            if creada:
                frappe.delete_doc("Account", creada, force=1, ignore_permissions=True)
                frappe.db.commit()

    def test_crea_la_cuenta_que_falta_donde_corresponde(self):
        """El camino de crearla, con un nombre que ninguna otra prueba usa: las cuentas normales
        acaban con asientos y ya no se pueden borrar, así que el resto del tiempo se reusan."""
        receta = dict(CUENTAS["cuenta_ieps"], nombre="IEPS de prueba borrable", alternas=())
        with patch.dict(CUENTAS, {"cuenta_ieps": receta}):
            frappe.db.set_single_value("Configuracion CxP", "cuenta_ieps", None)
            frappe.db.commit()
            nombre = None
            try:
                r = configurar_empresa(EMPRESA, RFC, dry_run=True)
                self.assertTrue([a for a in r["acciones"] if a.startswith(f"Crear la cuenta '{receta['nombre']}")],
                                r["acciones"])
                self.assertFalse(frappe.db.exists("Account", {"company": EMPRESA, "account_name": receta["nombre"]}))
                configurar_empresa(EMPRESA, RFC, dry_run=False)
                nombre = frappe.db.get_value("Account", {"company": EMPRESA, "account_name": receta["nombre"]}, "name")
                self.assertTrue(nombre, "la cuenta debería haberse creado")
                cuenta = frappe.get_doc("Account", nombre)
                self.assertEqual(cuenta.root_type, receta["root_type"])
                self.assertEqual(cuenta.account_type, receta["account_type"])
                self.assertEqual(cuenta.is_group, 0)
                self.assertTrue(cuenta.parent_account)
                self.assertEqual(frappe.db.get_single_value("Configuracion CxP", "cuenta_ieps"), nombre)
            finally:
                # Recién creada no tiene asientos, así que sí se puede borrar y no ensucia el sitio.
                # En un finally: si una aserción falla a medio camino, la cuenta no debe sobrevivir.
                frappe.db.set_single_value("Configuracion CxP", "cuenta_ieps", None)
                if nombre:
                    frappe.delete_doc("Account", nombre, force=1, ignore_permissions=True)
                frappe.db.commit()

    def test_no_crea_una_cuenta_si_el_nombre_ya_esta_ocupado(self):
        """Si ya existe una cuenta con el nombre completo que el script pondría, pero no sirve
        (es grupo, o de otro root_type: _buscar_cuenta no la encuentra), no hay que pisarla ni
        intentar un duplicado que Frappe rechazaría de todos modos: hay que avisar y parar."""
        receta = dict(CUENTAS["cuenta_ieps"], nombre="IEPS de prueba ocupada", alternas=())
        with patch.dict(CUENTAS, {"cuenta_ieps": receta}):
            frappe.db.set_single_value("Configuracion CxP", "cuenta_ieps", None)
            frappe.db.commit()
            abbr = frappe.db.get_value("Company", EMPRESA, "abbr")
            nombre_completo = f"{receta['nombre']} - {abbr}"
            padre = frappe.db.get_value("Account", {"company": EMPRESA, "root_type": "Asset", "is_group": 1,
                                                    "parent_account": ["is", "set"]}, "name", order_by="lft")
            frappe.get_doc({"doctype": "Account", "account_name": receta["nombre"], "parent_account": padre,
                            "company": EMPRESA, "root_type": "Asset", "is_group": 1}).insert(ignore_permissions=True)
            try:
                self.assertTrue(frappe.db.exists("Account", nombre_completo))
                with self.assertRaises(frappe.ValidationError):
                    configurar_empresa(EMPRESA, RFC, dry_run=True)
            finally:
                frappe.db.set_single_value("Configuracion CxP", "cuenta_ieps", None)
                frappe.delete_doc("Account", nombre_completo, force=1, ignore_permissions=True)
                frappe.db.commit()

    def test_las_cuentas_configuradas_quedan_del_tipo_correcto(self):
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

    def test_la_unidad_del_articulo_permite_decimales(self):
        """Los CFDI traen cantidades con fracción (10.26 kg). Si la unidad del artículo genérico
        está marcada como 'debe ser número entero' -- 'Nos' lo viene de fábrica en ERPNext --, la
        factura se rechaza; configurar_empresa tiene que quitarle esa marca."""
        uom = frappe.db.get_value("Item", ITEM_GENERICO, "stock_uom")
        self.assertTrue(uom, "el artículo genérico debería existir en el sitio de pruebas")
        frappe.db.set_value("UOM", uom, "must_be_whole_number", 1)
        frappe.db.commit()
        try:
            r = configurar_empresa(EMPRESA, RFC, dry_run=True)
            self.assertTrue([a for a in r["acciones"] if a.startswith(f"Permitir decimales en la unidad '{uom}'")],
                            r["acciones"])
            self.assertEqual(frappe.db.get_value("UOM", uom, "must_be_whole_number"), 1)   # el dry-run no tocó nada
            configurar_empresa(EMPRESA, RFC, dry_run=False)
            self.assertEqual(frappe.db.get_value("UOM", uom, "must_be_whole_number"), 0)
            # Y ya sin la marca, no hay nada más que reportar sobre la unidad.
            r2 = configurar_empresa(EMPRESA, RFC, dry_run=True)
            self.assertFalse([a for a in r2["acciones"] if a.startswith("Permitir decimales")], r2["acciones"])
        finally:
            frappe.db.set_value("UOM", uom, "must_be_whole_number", 0)
            frappe.db.commit()

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

    def test_no_se_puede_dar_a_mano_una_cuenta_por_pagar_en_otra_moneda(self):
        """A diferencia del root_type y el account_type (que sólo se avisan con REVISAR), la moneda
        se rechaza: una cuenta por pagar en otra moneda rompería las facturas."""
        moneda_empresa = frappe.db.get_value("Company", EMPRESA, "default_currency")
        otra_moneda = "USD" if moneda_empresa != "USD" else "MXN"
        padre = frappe.db.get_value("Account", {"company": EMPRESA, "root_type": "Liability", "is_group": 1,
                                                "parent_account": ["is", "set"]}, "name", order_by="lft")
        cuenta = frappe.get_doc({"doctype": "Account", "account_name": "Por pagar en otra moneda de prueba",
                                 "parent_account": padre, "company": EMPRESA, "root_type": "Liability",
                                 "is_group": 0, "account_type": "Payable",
                                 "account_currency": otra_moneda}).insert(ignore_permissions=True).name
        try:
            with self.assertRaises(frappe.ValidationError):
                configurar_empresa(EMPRESA, RFC, dry_run=True, cuentas={"default_payable_account": cuenta})
        finally:
            frappe.delete_doc("Account", cuenta, force=1, ignore_permissions=True)
            frappe.db.commit()

    def test_avisa_si_la_cuenta_a_mano_no_es_del_tipo_esperado(self):
        """El aviso REVISAR sale siempre: en dry-run y también cuando ya no hay nada que cambiar. Va
        en `avisos` y NO en `acciones`, para que `acciones` quede vacía cuando no hay nada por hacer
        (si no, la segunda corrida creería que sigue habiendo trabajo y haría commit sin cambios)."""
        olvidar_las_cuentas()
        gasto = una_cuenta(root_type="Expense")       # se pide como IVA acreditable, que es Asset
        a_mano = {"cuenta_iva_acreditable": gasto}
        r = configurar_empresa(EMPRESA, RFC, dry_run=True, cuentas=a_mano)
        self.assertTrue([a for a in r["avisos"] if a.startswith("REVISAR:") and gasto in a], r["avisos"])
        self.assertFalse([a for a in r["acciones"] if a.startswith("REVISAR:")], r["acciones"])
        configurar_empresa(EMPRESA, RFC, dry_run=False, cuentas=a_mano)
        r2 = configurar_empresa(EMPRESA, RFC, dry_run=False, cuentas=a_mano)
        self.assertTrue([a for a in r2["avisos"] if a.startswith("REVISAR:")], r2["avisos"])
        self.assertEqual(r2["acciones"], [])
        self.assertIn("aviso", r2["resumen"])
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

    def test_el_rfc_tiene_que_tener_forma_valida(self):
        with self.assertRaises(frappe.ValidationError):
            configurar_empresa(EMPRESA, "", dry_run=True)
        with self.assertRaises(frappe.ValidationError):
            configurar_empresa(EMPRESA, "MALO", dry_run=True)
