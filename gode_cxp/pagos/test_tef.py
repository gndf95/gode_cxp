import os
import unittest
from datetime import date
from decimal import Decimal

from gode_cxp.pagos.tef import TefInvalido, generar_tef, leer_tef, nombre_archivo

# Las muestras reales del banco no entran al repo (traen CLABEs de proveedores): viven en la VPS en
# /home/sergio/service-env/tef-muestras y docker/bench-pruebas.sh las monta aquí de sólo lectura.
MUESTRAS = "/home/frappe/tef-muestras"
LOTE_06 = {
    "contrato": "000181511777", "fecha": date(2026, 9, 17), "secuencial": 1, "empresa": "GASTRONOMICA DE ESPECIALIDADES GODE",
    "concepto": "pago gode", "naturaleza": "06", "sucursal_cargo": "7007", "cuenta_cargo": "8382129", "referencia_numerica": "0170926",
    "transferencias": [
        {"importe": Decimal("11938.04"), "beneficiario": "COMERCIALIZADORA ECOLIM SA DE CV/", "sucursal": "7005", "cuenta": "7479513"},
        {"importe": Decimal("848.75"), "beneficiario": "MARINTER SA DE CV/", "sucursal": "0189", "cuenta": "4315985"},
    ],
}
LOTE_12 = dict(LOTE_06, secuencial=2, naturaleza="12", transferencias=[
    {"importe": Decimal("13265.00"), "beneficiario": "AVICOLA,DEL CARMEN SA DE CV/", "clabe": "072180007090045065"},
    {"importe": Decimal("3275.10"), "beneficiario": "TRECE,SIETE GROUP SA DE CV/", "clabe": "014180655090628465"},
])


class TestGenerador(unittest.TestCase):
    def test_largos_y_fin_de_linea(self):
        datos = generar_tef(LOTE_12)
        self.assertTrue(datos.endswith(b"\r\n"))
        lineas = datos.split(b"\r\n")[:-1]
        self.assertEqual([len(l) for l in lineas], [124, 69, 217, 217, 52])
        self.assertEqual([l[:1] for l in lineas], [b"1", b"2", b"3", b"3", b"4"])
        datos.decode("ascii")

    def test_registro_1(self):
        l1 = generar_tef(LOTE_12).split(b"\r\n")[0].decode()
        self.assertEqual(l1, "1" + "000181511777" + "170926" + "0002" + "GASTRONOMICA DE ESPECIALIDADES GODE".ljust(36) + "pago gode".ljust(20) + "12" + " " * 40 + "C00")

    def test_registro_2_y_4(self):
        lineas = generar_tef(LOTE_12).split(b"\r\n")
        self.assertEqual(lineas[1].decode(), "2" + "1" + "001" + "000000000001654010" + "01" + "7007" + "8382129".zfill(20) + " " * 20)
        self.assertEqual(lineas[-2].decode(), "4" + "001" + "000002" + "000000000001654010" + "000001" + "000000000001654010")

    def test_registro_3_interbancario(self):
        l3 = generar_tef(LOTE_12).split(b"\r\n")[2].decode()
        self.assertEqual(l3[:5], "30001")
        self.assertEqual(l3[5:23], "000000000001326500")
        self.assertEqual(l3[23:25], "01")
        self.assertEqual(l3[25:45], "00072180007090045065")
        self.assertEqual(l3[45:85], "pago gode".ljust(40))
        self.assertEqual(l3[85:140], "AVICOLA,DEL CARMEN SA DE CV/".ljust(55))
        self.assertEqual(l3[140:180], " " * 40)
        self.assertEqual(l3[180:204], " " * 24)
        self.assertEqual(l3[204:208], "0072")
        self.assertEqual(l3[208:215], "0170926")
        self.assertEqual(l3[215:217], "00")

    def test_registro_3_banamex(self):
        l3 = generar_tef(LOTE_06).split(b"\r\n")[2].decode()
        self.assertEqual(l3[25:45], "00000000070057479513")
        self.assertEqual(l3[45:85], "0000170926".ljust(40))
        self.assertEqual(l3[140:180], "pago gode".ljust(40))
        self.assertEqual(l3[204:217], "0000" + "0000000" + "00")

    def test_nombre_archivo(self):
        self.assertEqual(nombre_archivo(date(2026, 9, 17), 2, "12"), "170926-0002-12.txt")

    def test_rechaza_datos_invalidos(self):
        for malo in (dict(LOTE_12, transferencias=[]), dict(LOTE_12, concepto="x" * 21), dict(LOTE_12, empresa="Ñ"),
                     dict(LOTE_12, transferencias=[dict(LOTE_12["transferencias"][0], importe=Decimal("0"))]),
                     dict(LOTE_12, transferencias=[dict(LOTE_12["transferencias"][0], beneficiario="A" * 56)]),
                     dict(LOTE_06, transferencias=[dict(LOTE_06["transferencias"][0], cuenta="123")]),
                     dict(LOTE_12, secuencial=10000)):
            with self.assertRaises(TefInvalido):
                generar_tef(malo)

    def test_ida_y_vuelta(self):
        for lote in (LOTE_06, LOTE_12):
            leido = leer_tef(generar_tef(lote))
            self.assertEqual(leido["naturaleza"], lote["naturaleza"])
            self.assertEqual(leido["secuencial"], lote["secuencial"])
            self.assertEqual([t["importe"] for t in leido["transferencias"]], [t["importe"] for t in lote["transferencias"]])
            self.assertEqual([t["beneficiario"] for t in leido["transferencias"]], [t["beneficiario"] for t in lote["transferencias"]])
            self.assertEqual(leido["total"], sum(t["importe"] for t in lote["transferencias"]))
            self.assertEqual([t["linea"] for t in leido["transferencias"]], [1, 2])

    def test_lector_rechaza_largo_malo(self):
        with self.assertRaises(TefInvalido):
            leer_tef(generar_tef(LOTE_12).replace(b"C00\r\n", b"C0\r\n", 1))


@unittest.skipUnless(os.path.isdir(MUESTRAS), "sin archivos de muestra del banco")
class TestMuestrasReales(unittest.TestCase):
    """Reconstruye cada archivo real a partir de su propia lectura y exige identidad byte a byte."""

    def _reconstruir(self, nombre):
        with open(os.path.join(MUESTRAS, nombre), "rb") as f:
            original = f.read()
        leido = leer_tef(original)
        regenerado = generar_tef(leido)
        self.assertEqual(regenerado, original, f"{nombre}: el archivo generado no es idéntico al aceptado por el banco")
        return leido

    def test_170926_banamex_06(self):
        leido = self._reconstruir("170926BANAMEX.txt")
        self.assertEqual((leido["naturaleza"], leido["secuencial"], leido["num_abonos"], leido["total"]), ("06", 1, 6, Decimal("102448.65")))

    def test_pagos2_12(self):
        leido = self._reconstruir("PAGOS2.txt")
        self.assertEqual((leido["naturaleza"], leido["secuencial"], leido["num_abonos"], leido["total"]), ("12", 2, 19, Decimal("456166.82")))
