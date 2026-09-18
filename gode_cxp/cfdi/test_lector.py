import unittest
from datetime import datetime

from gode_cxp.cfdi import ejemplos
from gode_cxp.cfdi.errores import CfdiInvalido
from gode_cxp.cfdi.lector import leer_cfdi


class TestLectorCfdi(unittest.TestCase):
    def test_ingreso_40(self):
        d = leer_cfdi(ejemplos.INGRESO_40)
        self.assertEqual(d["version"], "4.0")
        self.assertEqual(d["uuid"], "6F2C3D48-1234-4A5B-9C8D-ABCDEF012345")   # siempre en mayúsculas
        self.assertEqual(d["tipo_comprobante"], "I")
        self.assertEqual((d["serie"], d["folio"]), ("A", "1234"))
        self.assertEqual(d["fecha_emision"], datetime(2026, 9, 10, 10, 15))
        self.assertEqual(d["fecha_timbrado"], datetime(2026, 9, 10, 10, 16))
        self.assertEqual(d["rfc_emisor"], "AVI900101AB1")
        self.assertEqual(d["nombre_emisor"], "AVICOLA DEL CARMEN SA DE CV")
        self.assertEqual(d["rfc_receptor"], "GES200101ABC")
        self.assertEqual(d["uso_cfdi"], "G03")
        self.assertEqual((d["moneda"], d["tipo_cambio"]), ("MXN", 1.0))
        self.assertEqual((d["subtotal"], d["descuento"], d["total"]), (1000.0, 0.0, 1160.0))
        self.assertEqual((d["iva_trasladado"], d["ieps"], d["iva_retenido"], d["isr_retenido"]), (160.0, 0.0, 0.0, 0.0))
        self.assertEqual((d["metodo_pago"], d["forma_pago"]), ("PPD", "03"))
        self.assertEqual(len(d["conceptos"]), 1)
        c = d["conceptos"][0]
        self.assertEqual(c["descripcion"], "Pechuga de pollo")
        self.assertEqual((c["cantidad"], c["valor_unitario"], c["importe"], c["iva"]), (10.0, 100.0, 1000.0, 160.0))
        self.assertEqual(c["clave_unidad"], "KGM")
        self.assertEqual(d["cfdi_relacionados"], [])

    def test_ingreso_33_con_retenciones(self):
        d = leer_cfdi(ejemplos.INGRESO_33_RETENCIONES)
        self.assertEqual(d["version"], "3.3")
        self.assertEqual(d["uuid"], "A1B2C3D4-0000-4E5F-8A9B-000000000077")
        self.assertEqual(d["iva_trasladado"], 800.0)
        self.assertEqual(d["isr_retenido"], 500.0)
        self.assertEqual(d["iva_retenido"], 533.33)
        self.assertEqual(d["total"], 4766.67)
        self.assertEqual(d["descuento"], 0.0)                      # atributo ausente → 0
        c = d["conceptos"][0]
        self.assertEqual((c["isr_retenido"], c["iva_retenido"]), (500.0, 533.33))
        self.assertEqual(d["nombre_receptor"], "GASTRONOMICA DE ESPECIALIDADES GODE")

    def test_egreso_con_relacionados(self):
        d = leer_cfdi(ejemplos.EGRESO_40)
        self.assertEqual(d["tipo_comprobante"], "E")
        self.assertEqual(d["cfdi_relacionados"], [{"tipo_relacion": "01", "uuid": "6F2C3D48-1234-4A5B-9C8D-ABCDEF012345"}])
        self.assertEqual(d["total"], 232.0)

    def test_pago(self):
        d = leer_cfdi(ejemplos.PAGO_40)
        self.assertEqual(d["tipo_comprobante"], "P")
        self.assertEqual(d["uuid"], "55555555-2222-4333-8444-555555555555")
        self.assertEqual(d["total"], 0.0)
        self.assertEqual(d["moneda"], "XXX")

    def test_usd(self):
        d = leer_cfdi(ejemplos.USD_40)
        self.assertEqual((d["moneda"], d["tipo_cambio"]), ("USD", 18.5))

    def test_sin_timbre_es_invalido(self):
        with self.assertRaises(CfdiInvalido) as cm:
            leer_cfdi(ejemplos.SIN_TIMBRE)
        self.assertIn("timbre", str(cm.exception).lower())

    def test_basura_es_invalido(self):
        with self.assertRaises(CfdiInvalido):
            leer_cfdi(b"esto no es xml")
        with self.assertRaises(CfdiInvalido):
            leer_cfdi(b"<otro/>")

    def test_total_cuadra(self):
        for xml in (ejemplos.INGRESO_40, ejemplos.INGRESO_33_RETENCIONES, ejemplos.EGRESO_40):
            d = leer_cfdi(xml)
            calculado = round(d["subtotal"] - d["descuento"] + d["iva_trasladado"] + d["ieps"] - d["iva_retenido"] - d["isr_retenido"], 2)
            self.assertAlmostEqual(calculado, d["total"], places=2)
