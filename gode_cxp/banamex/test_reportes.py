"""Reportes 'Pagos por lote (COI)' y 'Facturas en lotes': lo que ve Contabilidad de un lote aplicado
y de las facturas que un lote sigue apartando.

Reusa el mismo escenario de banamex/test_aplicar.py: un lote con una transferencia que cubre dos
facturas con importes parciales (aplicado) y un segundo lote con una transferencia que el banco
rechaza (sigue apartando su factura).
"""
from datetime import date

import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.banamex.aplicar import aplicar_resultado, crear_resultado_desde_lote
from gode_cxp.cfdi import ejemplos
from gode_cxp.cuentas_por_pagar.report.facturas_en_lotes.facturas_en_lotes import execute as ejecutar_facturas_en_lotes
from gode_cxp.cuentas_por_pagar.report.pagos_por_lote_coi.pagos_por_lote_coi import execute as ejecutar_pagos_por_lote
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import cuenta_verificada, factura_aprobada, xml_con
from gode_cxp.pagos.lotes import crear_lotes, generar_archivo, marcar_transmitido
from gode_cxp.setup.produccion import configurar_pagos

CLABE_A = "072180007090045065"
FECHA = date(2026, 9, 17)
AUTORIZACION = "119938"
MOTIVO = "VERIFIQUE CARACTERES INVALIDOS EN E"

COLUMNAS_PAGOS = {"lote", "fecha_pago", "naturaleza", "secuencial", "autorizacion", "linea", "proveedor",
                  "rfc", "beneficiario_tef", "importe_transferido", "estado_pago", "clave_rastreo", "pago",
                  "factura", "folio", "uuid", "importe_aplicado"}
COLUMNAS_FACTURAS = {"factura", "proveedor", "folio", "fecha", "vencimiento", "total", "saldo", "lote",
                     "estado_lote", "estado_transferencia", "pago"}


class TestReportes(FrappeTestCase):
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
        self.fa1 = factura_aprobada(ejemplos.INGRESO_40)
        self.fa2 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "11111111-2222-3333-4444-555555555555", "77"))
        self.fa3 = factura_aprobada(xml_con(ejemplos.INGRESO_40, "22222222-3333-4444-5555-666666666666", "78"))
        cuenta_verificada(self.fa1.supplier, CLABE_A)

        # Lote 1: una transferencia que cubre las dos primeras facturas con importes parciales, aplicada.
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA,
                                [{"factura": self.fa1.name, "importe": 1000},
                                 {"factura": self.fa2.name, "importe": 160}])
        frappe.get_doc("Lote de Pago", nombre).submit()
        generar_archivo(nombre)
        marcar_transmitido(nombre, AUTORIZACION)
        self.lote = frappe.get_doc("Lote de Pago", nombre)
        r = crear_resultado_desde_lote(self.lote.name)
        for m in r.movimientos:
            m.estatus, m.clave_rastreo = "3", f"RASTREO{m.linea}"
        r.save()
        self.pago = aplicar_resultado(r.name)["creados"][0]
        self.lote.reload()

        # Lote 2: una transferencia de la tercera factura que el banco rechaza.
        (nombre2,) = crear_lotes(pruebas_comun.EMPRESA, FECHA,
                                 [{"factura": self.fa3.name, "importe": self.fa3.outstanding_amount}])
        frappe.get_doc("Lote de Pago", nombre2).submit()
        generar_archivo(nombre2)
        marcar_transmitido(nombre2, "1")
        self.lote_rechazado = frappe.get_doc("Lote de Pago", nombre2)
        r2 = crear_resultado_desde_lote(self.lote_rechazado.name)
        for m in r2.movimientos:
            m.estatus, m.motivo = "5", MOTIVO
        r2.save()
        aplicar_resultado(r2.name)
        self.lote_rechazado.reload()

    def tearDown(self):
        frappe.set_user("Administrator")

    # --- Pagos por lote (COI) ---------------------------------------------------------------------

    def test_columnas_de_pagos_por_lote_coi(self):
        columnas, _ = ejecutar_pagos_por_lote({"desde": FECHA, "hasta": FECHA})
        self.assertEqual({c["fieldname"] for c in columnas}, COLUMNAS_PAGOS)
        for c in columnas:
            self.assertTrue({"label", "fieldname", "fieldtype"} <= set(c))

    def test_pagos_por_lote_coi_solo_lo_aplicado_por_omision(self):
        _, datos = ejecutar_pagos_por_lote({"desde": FECHA, "hasta": FECHA})
        self.assertEqual(len(datos), 2)
        por_uuid = {fila["uuid"]: fila for fila in datos}
        self.assertEqual(por_uuid[self.fa1.cfdi_uuid]["importe_aplicado"], 1000)
        self.assertEqual(por_uuid[self.fa2.cfdi_uuid]["importe_aplicado"], 160)
        for fila in datos:
            self.assertEqual(fila["pago"], self.pago)
            self.assertEqual(fila["estado_pago"], "Aplicado")
            self.assertEqual(fila["lote"], self.lote.name)
            self.assertEqual(fila["rfc"], frappe.db.get_value("Supplier", self.fa1.supplier, "tax_id"))

    def test_pagos_por_lote_coi_incluye_rechazadas_si_se_pide(self):
        _, datos = ejecutar_pagos_por_lote({"desde": FECHA, "hasta": FECHA, "solo_aplicados": 0})
        self.assertEqual(len(datos), 3)
        rechazadas = [f for f in datos if f["estado_pago"] == "Rechazado"]
        self.assertEqual(len(rechazadas), 1)
        self.assertEqual(rechazadas[0]["factura"], self.fa3.name)
        self.assertEqual(rechazadas[0]["lote"], self.lote_rechazado.name)
        self.assertIsNone(rechazadas[0]["pago"])

    def test_pagos_por_lote_coi_filtra_por_lote(self):
        _, datos = ejecutar_pagos_por_lote({"desde": FECHA, "hasta": FECHA, "lote": self.lote_rechazado.name,
                                            "solo_aplicados": 0})
        self.assertEqual({f["lote"] for f in datos}, {self.lote_rechazado.name})

    def test_pagos_por_lote_coi_fuera_del_rango_de_fechas_no_sale(self):
        _, datos = ejecutar_pagos_por_lote({"desde": date(2026, 1, 1), "hasta": date(2026, 1, 2)})
        self.assertEqual(datos, [])

    # --- Facturas en lotes -------------------------------------------------------------------------

    def test_columnas_de_facturas_en_lotes(self):
        columnas, _ = ejecutar_facturas_en_lotes({})
        self.assertEqual({c["fieldname"] for c in columnas}, COLUMNAS_FACTURAS)
        for c in columnas:
            self.assertTrue({"label", "fieldname", "fieldtype"} <= set(c))

    def test_facturas_en_lotes_de_un_proveedor(self):
        # fa1 y fa2 quedaron libres (el lote 1 quedó Aplicado y su saldo se liberó); solo fa3 sigue
        # apartada por el lote rechazado, aunque las tres facturas son del mismo proveedor.
        _, datos = ejecutar_facturas_en_lotes({"proveedor": self.fa3.supplier})
        self.assertEqual(len(datos), 1)
        fila = datos[0]
        self.assertEqual((fila["factura"], fila["lote"], fila["estado_lote"], fila["estado_transferencia"]),
                         (self.fa3.name, self.lote_rechazado.name, "Rechazado", "Rechazado"))
        self.assertIsNone(fila["pago"])

    def test_facturas_en_lotes_filtra_por_estado_del_lote(self):
        _, rechazadas = ejecutar_facturas_en_lotes({"estado_lote": "Rechazado"})
        self.assertEqual({f["factura"] for f in rechazadas}, {self.fa3.name})
        _, aplicadas = ejecutar_facturas_en_lotes({"estado_lote": "Aplicado"})
        self.assertEqual(aplicadas, [])
