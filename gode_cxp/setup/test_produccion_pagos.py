"""Pruebas de `configurar_pagos`: banco y cuenta de cargo de la empresa + datos del archivo TEF."""
import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.facturas import pruebas_comun
from gode_cxp.setup.produccion import configurar_pagos

DATOS = dict(contrato="000181511777", sucursal="7007", cuenta="8382129",
             nombre_tef="GASTRONOMICA DE ESPECIALIDADES GODE", concepto="pago gode")


class TestProduccionPagos(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()
        cls.banco_erp = frappe.db.get_value("Account", {"company": pruebas_comun.EMPRESA, "account_type": "Bank", "is_group": 0}, "name")
        assert cls.banco_erp, "el catálogo de pruebas necesita una cuenta contable de tipo Bank"

    def test_dry_run_no_escribe(self):
        frappe.db.set_single_value("Configuracion CxP", "contrato_banamex", "")
        r = configurar_pagos(pruebas_comun.EMPRESA, cuenta_banco_erp=self.banco_erp, dry_run=True, **DATOS)
        self.assertTrue(any("contrato" in a.lower() for a in r["acciones"]))
        self.assertFalse(frappe.db.get_single_value("Configuracion CxP", "contrato_banamex"))

    def test_aplicar_es_idempotente(self):
        r1 = configurar_pagos(pruebas_comun.EMPRESA, cuenta_banco_erp=self.banco_erp, dry_run=False, **DATOS)
        r2 = configurar_pagos(pruebas_comun.EMPRESA, cuenta_banco_erp=self.banco_erp, dry_run=False, **DATOS)
        self.assertEqual(r2["acciones"], [])
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
                     dict(DATOS, nombre_tef="GASTRONÓMICA DE ESPECIALIDADES GODE"), dict(DATOS, concepto="pago gode para proveedores x")):
            with self.assertRaises(frappe.ValidationError):
                configurar_pagos(pruebas_comun.EMPRESA, cuenta_banco_erp=self.banco_erp, dry_run=True, **malo)
