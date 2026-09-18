"""Pruebas de `configurar_pagos`: banco y cuenta de cargo de la empresa + datos del archivo TEF."""
import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.facturas import pruebas_comun
from gode_cxp.setup.campos import CAMPOS
from gode_cxp.setup.produccion import (BANCO_EMPRESA, NADA_QUE_HACER_PAGOS, NOMBRE_CUENTA_EMPRESA,
                                       configurar_pagos)

DATOS = dict(contrato="000181511777", sucursal="7007", cuenta="8382129",
             nombre_tef="GASTRONOMICA DE ESPECIALIDADES GODE", concepto="pago gode")
CUENTA_ESPERADA = f"{NOMBRE_CUENTA_EMPRESA} - {BANCO_EMPRESA}"


def borrar_la_cuenta_bancaria():
    """Deja el sitio sin cuenta bancaria de empresa ni banco 'Banamex', para poder ejercitar las
    guardas de los inserts. Hay que soltar primero el Link de Configuracion CxP: si no, el borrado
    se queja de que la cuenta está en uso."""
    frappe.db.set_single_value("Configuracion CxP", "cuenta_bancaria_empresa", "")
    for nombre in frappe.get_all("Bank Account", filters={"bank": BANCO_EMPRESA}, pluck="name"):
        frappe.delete_doc("Bank Account", nombre, force=1, ignore_permissions=True, delete_permanently=True)
    if frappe.db.exists("Bank", BANCO_EMPRESA) and not frappe.db.exists("Bank Account", {"bank": BANCO_EMPRESA}):
        frappe.delete_doc("Bank", BANCO_EMPRESA, force=1, ignore_permissions=True, delete_permanently=True)


class TestProduccionPagos(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()
        cls.banco_erp = frappe.db.get_value("Account", {"company": pruebas_comun.EMPRESA, "account_type": "Bank", "is_group": 0}, "name")
        assert cls.banco_erp, "el catálogo de pruebas necesita una cuenta contable de tipo Bank"

    def aplicar(self, **extra):
        return configurar_pagos(pruebas_comun.EMPRESA, cuenta_banco_erp=self.banco_erp,
                                **dict(DATOS, **extra))

    def dejar_todo_configurado(self):
        """Cleanup común: vuelve a dejar el banco, la cuenta bancaria y la Configuración CxP como las
        esperan las demás pruebas (y los demás módulos de la suite)."""
        self.aplicar(dry_run=False)

    def test_dry_run_no_escribe(self):
        antes = frappe.db.get_single_value("Configuracion CxP", "contrato_banamex")
        self.addCleanup(frappe.db.set_single_value, "Configuracion CxP", "contrato_banamex", antes)
        frappe.db.set_single_value("Configuracion CxP", "contrato_banamex", "")
        r = self.aplicar(dry_run=True)
        self.assertTrue(any("contrato" in a.lower() for a in r["acciones"]))
        self.assertFalse(frappe.db.get_single_value("Configuracion CxP", "contrato_banamex"))

    def test_aplicar_es_idempotente(self):
        self.aplicar(dry_run=False)
        r2 = self.aplicar(dry_run=False)
        self.assertEqual(r2["acciones"], [])
        self.assertEqual(r2["avisos"], [])
        self.assertEqual(r2["resumen"], NADA_QUE_HACER_PAGOS)
        conf = frappe.get_doc("Configuracion CxP")
        self.assertEqual((conf.contrato_banamex, conf.cuenta_cargo_sucursal, conf.cuenta_cargo_numero), ("000181511777", "7007", "8382129"))
        self.assertEqual((conf.nombre_empresa_tef, conf.concepto_tef, conf.referencia_numerica_modo), ("GASTRONOMICA DE ESPECIALIDADES GODE", "pago gode", "Fecha del lote"))
        self.assertEqual(conf.cuenta_banco_erp, self.banco_erp)
        self.assertEqual(conf.modo_pago_transferencia, "Transferencia bancaria")
        cuenta = frappe.get_doc("Bank Account", conf.cuenta_bancaria_empresa)
        self.assertEqual((cuenta.is_company_account, cuenta.company, cuenta.account, cuenta.bank), (1, pruebas_comun.EMPRESA, self.banco_erp, "Banamex"))

    def test_campos_nuevos_existen(self):
        for dt, campo in (("Bank Account", "clabe"), ("Bank Account", "nombre_tef"), ("Bank Account", "verificada"),
                          ("Payment Entry", "lote_pago"), ("Payment Entry", "clave_rastreo"), ("Purchase Invoice", "en_lote")):
            self.assertTrue(frappe.get_meta(dt).has_field(campo), f"{dt}.{campo}")

    def test_rechaza_datos_mal_formados(self):
        for malo in (dict(DATOS, contrato="18151177"), dict(DATOS, sucursal="70071"), dict(DATOS, cuenta="83821"),
                     dict(DATOS, nombre_tef="GASTRONÓMICA DE ESPECIALIDADES GODE"), dict(DATOS, concepto="pago gode para proveedores x"),
                     # El concepto va a un archivo ASCII de ancho fijo: ni acentos ni saltos de línea.
                     dict(DATOS, concepto="pago gode ñ"), dict(DATOS, concepto="pago\ngode")):
            with self.assertRaises(frappe.ValidationError):
                configurar_pagos(pruebas_comun.EMPRESA, cuenta_banco_erp=self.banco_erp, dry_run=True, **malo)

    def test_las_secciones_nuevas_no_se_tragan_campos_estandar(self):
        """Un Section Break personalizado se lleva consigo todo lo que viene después en el meta hasta
        el siguiente corte de layout. Si entre la sección nueva y ese corte queda un campo ESTÁNDAR,
        ese campo hereda el `depends_on` (o el `collapsible`) de la sección nueva y desaparece del
        formulario: así `sec_tef` y `sec_lote` esconderían `branch_code` y `clearance_date`.

        Se revisan TODAS las secciones que agrega la app, no sólo las de pagos: el error es de la
        forma de declarar los campos, no de un DocType en particular."""
        # Lo que cierra una sección en el formulario: otra sección, una pestaña o un 'Fold'
        # (frappe/model/__init__.py los lista juntos como campos de layout).
        cortes = ("Section Break", "Tab Break", "Fold")
        for dt, definiciones in CAMPOS.items():
            for seccion in [c["fieldname"] for c in definiciones if c["fieldtype"] == "Section Break"]:
                campos = frappe.get_meta(dt).fields
                nombres = [f.fieldname for f in campos]
                self.assertIn(seccion, nombres, f"{dt}: la sección '{seccion}' no está en el meta")
                dentro = []
                for campo in campos[nombres.index(seccion) + 1:]:
                    if campo.fieldtype in cortes:
                        break
                    dentro.append(campo)
                ajenos = [f.fieldname for f in dentro if not f.get("is_custom_field")]
                self.assertEqual(ajenos, [], f"{dt}: la sección '{seccion}' se tragó {ajenos}")

    def test_dry_run_sin_cuenta_bancaria_promete_crear_banco_y_cuenta(self):
        """Con el sitio limpio de banco y cuenta bancaria, el dry-run tiene que prometer los dos
        inserts y no hacer ninguno."""
        self.addCleanup(self.dejar_todo_configurado)
        borrar_la_cuenta_bancaria()
        r = self.aplicar(dry_run=True)
        self.assertTrue([a for a in r["acciones"] if a.startswith(f"Crear el banco '{BANCO_EMPRESA}'")], r["acciones"])
        self.assertTrue([a for a in r["acciones"] if a.startswith(f"Crear la cuenta bancaria de la empresa '{CUENTA_ESPERADA}'")], r["acciones"])
        self.assertFalse(frappe.db.exists("Bank", BANCO_EMPRESA))
        self.assertFalse(frappe.db.exists("Bank Account", CUENTA_ESPERADA))

    def test_no_escribe_nada_si_falta_el_modo_de_pago(self):
        """La comprobación del modo de pago va ANTES del primer insert: si falta, no puede quedar a
        medias un banco recién creado."""
        antes = frappe.db.get_single_value("Configuracion CxP", "modo_pago_transferencia")
        self.addCleanup(self.dejar_todo_configurado)
        self.addCleanup(frappe.db.set_single_value, "Configuracion CxP", "modo_pago_transferencia", antes)
        borrar_la_cuenta_bancaria()
        frappe.db.set_single_value("Configuracion CxP", "modo_pago_transferencia", "Modo que no existe")
        with self.assertRaises(frappe.ValidationError):
            self.aplicar(dry_run=False)
        self.assertFalse(frappe.db.exists("Bank", BANCO_EMPRESA))

    def test_reusa_la_cuenta_bancaria_de_empresa_que_ya_existe(self):
        """ERPNext no deja dos Bank Account sobre la misma cuenta contable, así que si el sitio ya
        tiene una cuenta de empresa hay que reusarla en vez de prometer un insert imposible."""
        self.addCleanup(self.dejar_todo_configurado)
        borrar_la_cuenta_bancaria()
        frappe.get_doc({"doctype": "Bank", "bank_name": BANCO_EMPRESA}).insert(ignore_permissions=True)
        a_mano = frappe.get_doc({"doctype": "Bank Account", "account_name": "Cuenta vieja GODE",
                                 "bank": BANCO_EMPRESA, "is_company_account": 1,
                                 "company": pruebas_comun.EMPRESA, "account": self.banco_erp,
                                 }).insert(ignore_permissions=True).name
        self.addCleanup(borrar_la_cuenta_bancaria)   # LIFO: primero se borra la de a mano, luego se reconfigura
        r = self.aplicar(dry_run=True)
        self.assertTrue([a for a in r["acciones"] if a.startswith(f"Reusar la cuenta bancaria '{a_mano}'")], r["acciones"])
        self.assertFalse([a for a in r["acciones"] if a.startswith("Crear la cuenta bancaria")], r["acciones"])
        cuantas = frappe.db.count("Bank Account", {"bank": BANCO_EMPRESA})
        self.aplicar(dry_run=False)
        self.assertEqual(frappe.db.count("Bank Account", {"bank": BANCO_EMPRESA}), cuantas)
        self.assertEqual(frappe.db.get_single_value("Configuracion CxP", "cuenta_bancaria_empresa"), a_mano)
        # Y con la cuenta ya configurada NO queda nada por hacer: "Reusar" no puede repetirse en cada
        # corrida. En producción la cuenta que ya existe no se llamará 'Banamex GODE - Banamex', así
        # que compararla sólo con el nombre esperado dejaría la función sin ser idempotente.
        r2 = self.aplicar(dry_run=False)
        self.assertEqual(r2["acciones"], [])
        self.assertEqual(r2["resumen"], NADA_QUE_HACER_PAGOS)

    def test_no_reusa_una_cuenta_bancaria_que_no_es_de_la_empresa(self):
        """El último recurso para encontrar la cuenta de cargo (buscar por la cuenta contable) tiene
        que exigir además `is_company_account` y la empresa: una cuenta bancaria de otro dueño ligada
        a la misma cuenta contable no es la cuenta de cargo de GODE."""
        self.addCleanup(self.dejar_todo_configurado)
        borrar_la_cuenta_bancaria()
        frappe.get_doc({"doctype": "Bank", "bank_name": BANCO_EMPRESA}).insert(ignore_permissions=True)
        ajena = frappe.get_doc({"doctype": "Bank Account", "account_name": "Cuenta ajena",
                                "bank": BANCO_EMPRESA, "is_company_account": 0,
                                "account": self.banco_erp}).insert(ignore_permissions=True).name
        self.addCleanup(borrar_la_cuenta_bancaria)   # LIFO: primero se borra la ajena, luego se reconfigura
        r = self.aplicar(dry_run=True)
        self.assertFalse([a for a in r["acciones"] if ajena in a], r["acciones"])
        self.assertTrue([a for a in r["acciones"]
                         if a.startswith(f"Crear la cuenta bancaria de la empresa '{CUENTA_ESPERADA}'")],
                        r["acciones"])
