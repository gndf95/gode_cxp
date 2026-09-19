"""Lo que contesta el banco: el CSV que se exporta de BancaNet, el archivo de ancho fijo de
exportación (layout C) y el Resultado Bancario que Tesorería captura o importa.

La lectura del archivo es pura (no toca frappe) y se prueba con `unittest`; el alta del resultado
desde el lote y la carga del archivo necesitan el sitio de pruebas.
"""
import unittest
from datetime import date
from decimal import Decimal

import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.banamex import api, aplicar
from gode_cxp.banamex.respuesta import CANCELADO, RECHAZADO, RespuestaInvalida, leer_respuesta
from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import cuenta_verificada, factura_aprobada, usuario
from gode_cxp.pagos.lotes import crear_lotes, generar_archivo, marcar_transmitido
from gode_cxp.pagos.tef import L3, generar_tef
from gode_cxp.setup.produccion import configurar_pagos

CSV = b"""Consecutivo,Beneficiario,Cuenta,Importe,Estatus,Descripcion,Clave de rastreo
1,AVICOLA DEL CARMEN SA DE CV,072180007090045065,"13,265.00",3,APLICADO,2026091740012BNET0001
2,TRECE SIETE GROUP SA DE CV,014180655090628465,3275.10,5,VERIFIQUE CARACTERES INVALIDOS EN E,
"""
LOTE = {"contrato": "000181511777", "fecha": date(2026, 9, 17), "secuencial": 2, "empresa": "GASTRONOMICA DE ESPECIALIDADES GODE",
        "concepto": "pago gode", "naturaleza": "12", "sucursal_cargo": "7007", "cuenta_cargo": "8382129", "referencia_numerica": "0170926",
        "transferencias": [{"importe": Decimal("13265.00"), "beneficiario": "AVICOLA,DEL CARMEN SA DE CV/", "clabe": "072180007090045065"},
                           {"importe": Decimal("3275.10"), "beneficiario": "TRECE,SIETE GROUP SA DE CV/", "clabe": "014180655090628465"}]}

CLABE_12 = "072180007090045065"
FECHA = date(2026, 9, 17)
TESORERIA, REVISOR = "prueba.tesoreria@cxp.local", "prueba.revisor@cxp.local"


def exportacion(estatus=("3", "5"), errores=("0000", "0012"),
                mensajes=("", "VERIFIQUE, CARACTERES INVALIDOS"), estatus_archivo="30",
                autorizacion="000000119938", salto="\r\n", recortar=False,
                originado=("    ", "    "), autorizaciones=None):
    """El archivo de EXPORTACIÓN del banco, armado sobre el de importación que ya sabemos generar.

    Layout de docs/banamex-formatos.md (sección "Exportación"): registro 1 + estatus del archivo (2) +
    autorización (12); registro 2 + importe de devolución (18); cada registro 3 + autorización (12) +
    estatus (1) + error originado (4, blancos) + número de error (4) + mensaje (31); registro 4 igual.
    """
    registros = generar_tef(LOTE).decode("ascii").split("\r\n")[:-1]
    r1, r2, r4, r3s = registros[0], registros[1], registros[-1], registros[2:-1]
    lineas = [r1 + estatus_archivo + autorizacion, r2 + "0" * 18]
    for i, r3 in enumerate(r3s):
        # El banco sólo devuelve autorización de lo que sí pagó; lo rechazado viene en ceros.
        aut = autorizaciones[i] if autorizaciones else (autorizacion if estatus[i] == "3" else "0" * 12)
        lineas.append(r3 + aut + estatus[i] + originado[i] + errores[i] + mensajes[i].ljust(31))
    lineas.append(r4)
    if recortar:
        lineas = [l.rstrip() for l in lineas]
    return (salto.join(lineas) + salto).encode("ascii")


class TestLectura(unittest.TestCase):
    def test_csv_del_portal(self):
        r = leer_respuesta(CSV, "respuesta.csv")
        self.assertEqual(len(r["movimientos"]), 2)
        m1, m2 = r["movimientos"]
        self.assertEqual((m1["linea"], m1["importe"], m1["estatus"], m1["clave_rastreo"]),
                         (1, Decimal("13265.00"), "3", "2026091740012BNET0001"))
        self.assertEqual((m2["estatus"], m2["motivo"]), ("5", "VERIFIQUE CARACTERES INVALIDOS EN E"))
        self.assertEqual((r["num_aplicados"], r["num_rechazados"]), (1, 1))
        # Un CSV no dice nada del archivo completo ni trae el total de control del banco.
        self.assertEqual((r["estatus_archivo"], r["autorizacion"], r["total_archivo"]), (None, None, None))

    def test_csv_con_punto_y_coma_y_columnas_en_otro_orden(self):
        csv = b"Estatus;Importe;Nombre;CLABE\n3;100.50;X Y;072180007090045065\n"
        r = leer_respuesta(csv, "x.csv")
        self.assertEqual((r["movimientos"][0]["importe"], r["movimientos"][0]["cuenta"], r["movimientos"][0]["linea"]),
                         (Decimal("100.50"), "072180007090045065", 1))

    def test_layout_c_con_estatus(self):
        r = leer_respuesta(exportacion(), "170926-0002-12.txt")
        self.assertEqual(r["estatus_archivo"], "30")
        self.assertEqual(r["autorizacion"], "119938")
        self.assertEqual([m["estatus"] for m in r["movimientos"]], ["3", "5"])
        self.assertEqual(r["movimientos"][0]["autorizacion"], "119938")
        self.assertEqual(r["movimientos"][0]["motivo"], "")
        self.assertEqual(r["movimientos"][0]["cuenta"], "072180007090045065")
        self.assertIn("VERIFIQUE", r["movimientos"][1]["motivo"])
        self.assertEqual(r["movimientos"][1]["autorizacion"], "")
        self.assertEqual(r["total_archivo"], Decimal("16540.10"))
        self.assertEqual((r["num_aplicados"], r["num_rechazados"]), (1, 1))

    def test_layout_c_tolera_lf_y_espacios_recortados(self):
        """El archivo pasa por el correo, por Windows y por algún editor antes de llegar: puede
        aparecer con LF en vez de CRLF y sin los espacios de relleno del final de cada línea."""
        r = leer_respuesta(exportacion(salto="\n", recortar=True), "respuesta.txt")
        self.assertEqual([m["estatus"] for m in r["movimientos"]], ["3", "5"])
        self.assertEqual(r["total_archivo"], Decimal("16540.10"))

    def test_un_archivo_rechazado_rechaza_todas_sus_transferencias(self):
        """32 = el banco rechazó el archivo completo; no pagó nada, aunque no venga estatus por línea."""
        datos = exportacion(estatus=(" ", " "), errores=("    ", "    "), mensajes=("", ""),
                            estatus_archivo="32", autorizacion=" " * 12)
        r = leer_respuesta(datos, "respuesta.txt")
        self.assertEqual(r["estatus_archivo"], "32")
        self.assertEqual([m["estatus"] for m in r["movimientos"]], ["5", "5"])
        self.assertIn("rechazado", r["movimientos"][0]["motivo"])
        self.assertEqual((r["num_aplicados"], r["num_rechazados"]), (0, 2))

    def test_un_archivo_rechazado_tumba_tambien_las_lineas_que_dicen_aplicado(self):
        """32 / 10 mandan sobre la línea: si el banco tumbó el archivo COMPLETO no pagó nada, así que
        un '3' por transferencia es una contradicción y no una excepción. Se fuerza a rechazada."""
        for archivo, palabra in ((RECHAZADO, "rechazado"), (CANCELADO, "cancelado")):
            with self.subTest(archivo=archivo):
                r = leer_respuesta(exportacion(estatus=("3", "3"), estatus_archivo=archivo), "x.txt")
                self.assertEqual([m["estatus"] for m in r["movimientos"]], ["5", "5"])
                self.assertEqual((r["num_aplicados"], r["num_rechazados"]), (0, 2))
                self.assertIn(palabra, r["movimientos"][0]["motivo"])

    def test_un_estatus_de_archivo_desconocido_es_el_canario_de_las_posiciones(self):
        """El estatus del archivo sólo puede ser 30, 32 o 10: cualquier otra cosa en esa posición
        significa que los campos del layout no están donde los esperamos."""
        with self.assertRaisesRegex(RespuestaInvalida, "layout"):
            leer_respuesta(exportacion(estatus_archivo="99"), "x.txt")

    def test_el_campo_error_originado_tiene_que_venir_en_blancos(self):
        with self.assertRaisesRegex(RespuestaInvalida, "layout"):
            leer_respuesta(exportacion(originado=("XXXX", "    ")), "x.txt")

    def test_la_autorizacion_de_la_linea_tiene_que_ser_digitos_o_blancos(self):
        with self.assertRaisesRegex(RespuestaInvalida, "layout"):
            leer_respuesta(exportacion(autorizaciones=("BNET00119938", "000000000000")), "x.txt")

    def test_un_archivo_corrido_un_caracter_no_se_lee_a_ciegas(self):
        """El canario de verdad: un archivo con un carácter de más en un registro 3 deja todos los
        campos de respuesta corridos, y leerlo como si nada sería inventarse el estatus del banco."""
        for desplazar in (1, -1):
            with self.subTest(desplazar=desplazar):
                lineas = exportacion().decode("ascii").split("\r\n")
                for i, linea in enumerate(lineas):
                    if linea[:1] == "3":
                        # Se mete (o se quita) un carácter justo donde termina el cuerpo de importación,
                        # conservando el largo del registro para que no salte el chequeo de longitud.
                        lineas[i] = (linea[:L3] + " " + linea[L3:-1] if desplazar == 1
                                     else linea[:L3] + linea[L3 + 1:] + " ")
                        break
                with self.assertRaisesRegex(RespuestaInvalida, "layout"):
                    leer_respuesta("\r\n".join(lineas).encode("ascii"), "corrido.txt")

    def test_el_archivo_que_se_le_subio_al_banco_no_es_una_respuesta(self):
        with self.assertRaises(RespuestaInvalida):
            leer_respuesta(generar_tef(LOTE), "170926-0002-12.txt")

    def test_rechaza_basura(self):
        with self.assertRaises(RespuestaInvalida):
            leer_respuesta(b"hola", "x.csv")
        with self.assertRaises(RespuestaInvalida):
            leer_respuesta(b"Importe,Estatus\nabc,3\n", "x.csv")
        with self.assertRaises(RespuestaInvalida):
            leer_respuesta(b"", "x.csv")
        with self.assertRaises(RespuestaInvalida):        # estatus que no es 3 ni 5
            leer_respuesta(b"Importe,Estatus\n100,9\n", "x.csv")
        with self.assertRaises(RespuestaInvalida):        # sin las columnas mínimas
            leer_respuesta(b"Beneficiario,Cuenta\nX Y,0721800070900450\n", "x.csv")

    def test_un_importe_vacio_no_es_cero(self):
        """Un renglón con el importe en blanco es un archivo que no entendimos, no una transferencia
        de cero pesos: darlo por bueno sería cruzarlo con la transferencia equivocada."""
        with self.assertRaisesRegex(RespuestaInvalida, "vacío"):
            leer_respuesta(b"Importe,Estatus\n,3\n", "x.csv")

    def test_un_importe_con_coma_decimal_no_se_adivina(self):
        """'3.275,10' son 3275.10 en Europa y 327510 si se le quitan las comas: no se adivina."""
        for crudo in (b"3.275,10", b"3275,10", b"1,5"):
            with self.subTest(crudo=crudo):
                with self.assertRaisesRegex(RespuestaInvalida, "coma"):
                    leer_respuesta(b"Importe,Estatus\n" + crudo + b",3\n", "x.csv")

    def test_el_separador_de_miles_si_se_entiende(self):
        r = leer_respuesta(b'Importe,Estatus\n"13,265.00",3\n', "x.csv")
        self.assertEqual(r["movimientos"][0]["importe"], Decimal("13265.00"))

    def test_el_nombre_del_archivo_sale_en_el_mensaje(self):
        with self.assertRaisesRegex(RespuestaInvalida, "respuesta-del-banco.csv"):
            leer_respuesta(b"   ", "respuesta-del-banco.csv")


class TestResultadoBancario(FrappeTestCase):
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
        usuario(TESORERIA, "CxP Tesoreria")
        usuario(REVISOR, "CxP Revisor")
        self.fa = factura_aprobada(ejemplos.INGRESO_40)
        self.cta = cuenta_verificada(self.fa.supplier, CLABE_12)
        self.beneficiario = frappe.db.get_value("Bank Account", self.cta, "nombre_tef")

    def tearDown(self):
        frappe.set_user("Administrator")

    def _lote_transmitido(self, importe=100):
        (nombre,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa.name, "importe": importe}])
        frappe.get_doc("Lote de Pago", nombre).submit()
        generar_archivo(nombre)
        marcar_transmitido(nombre, "119938")
        return nombre

    def _csv(self, importe="100.00", estatus="3", linea="1"):
        """Lo que se exporta del portal para este lote. El beneficiario va entre comillas porque el
        nombre del TEF lleva coma (NOMBRE,PATERNO/MATERNO)."""
        return (f"Consecutivo,Beneficiario,Cuenta,Importe,Estatus,Descripcion,Clave de rastreo\n"
                f'{linea},"{self.beneficiario}",{CLABE_12},{importe},{estatus},APLICADO,2026091740012BNET0001\n'
                ).encode("utf-8")

    def _subir(self, contenido, nombre="respuesta.csv", resultado=None, privado=1):
        adjunto = {"attached_to_doctype": "Resultado Bancario", "attached_to_name": resultado} if resultado else {}
        return frappe.get_doc({"doctype": "File", "file_name": nombre, "content": contenido,
                               "is_private": privado, **adjunto}).insert(ignore_permissions=True).file_url

    def _solo_ve(self, doctype, valor, correo=TESORERIA):
        """Le deja a `correo` un único documento permitido de `doctype`, o sea le niega los demás:
        es la forma real de ejercitar las User Permissions por empresa (misma técnica que
        pagos/test_api.py). `ignore_links` porque `valor` no tiene por qué existir."""
        up = frappe.get_doc({"doctype": "User Permission", "user": correo, "allow": doctype, "for_value": valor})
        up.flags.ignore_links = True
        up.insert(ignore_permissions=True)
        self.addCleanup(frappe.clear_cache, user=correo)
        self.addCleanup(frappe.delete_doc, "User Permission", up.name, force=1, ignore_permissions=True)
        frappe.clear_cache(user=correo)

    def test_captura_manual_desde_el_lote(self):
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        self.assertEqual((r.origen, r.estado, r.autorizacion), ("Captura manual", "Importado", "119938"))
        self.assertEqual(len(r.movimientos), 1)
        m = r.movimientos[0]
        self.assertEqual((m.linea, m.beneficiario, m.cuenta, m.importe), (1, self.beneficiario, CLABE_12, 100))
        # Sin estatus: eso es justo lo que Tesorería va a capturar a mano.
        self.assertFalse(m.estatus)
        self.assertEqual((r.num_aplicados, r.num_rechazados, r.total_calculado), (0, 0, 0))
        self.assertEqual(r.naturaleza, "12")

    def test_solo_se_captura_el_resultado_de_un_lote_transmitido(self):
        (lote,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.fa.name, "importe": 100}])
        with self.assertRaises(frappe.ValidationError):
            aplicar.crear_resultado_desde_lote(lote)

    def test_capturar_3_a_mano_cuadra_el_resultado(self):
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        r.movimientos[0].estatus = "3"
        r.movimientos[0].clave_rastreo = "2026091740012BNET0001"
        r.save()
        self.assertEqual((r.estado, r.num_aplicados, r.num_rechazados), ("Importado", 1, 0))
        self.assertEqual(r.total_calculado, 100)
        self.assertFalse(r.diferencias)
        self.assertEqual(r.movimientos[0].transferencia_idx, 1)

    def test_cargar_archivo_reemplaza_los_movimientos(self):
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        file_url = self._subir(self._csv(), resultado=r.name)
        r = aplicar.cargar_archivo(r.name, file_url)
        self.assertEqual((r.origen, r.estado, r.archivo), ("Archivo del portal", "Importado", file_url))
        self.assertEqual(len(r.movimientos), 1)
        self.assertEqual((r.movimientos[0].estatus, r.movimientos[0].clave_rastreo),
                         ("3", "2026091740012BNET0001"))
        self.assertEqual((r.num_aplicados, r.total_calculado), (1, 100))
        self.assertFalse(r.diferencias)

    def test_un_importe_distinto_al_del_lote_deja_el_resultado_con_diferencias(self):
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        r = aplicar.cargar_archivo(r.name, self._subir(self._csv(importe="99.00"), resultado=r.name))
        self.assertEqual(r.estado, "Con diferencias")
        self.assertIn("99", r.diferencias)
        self.assertEqual(frappe.db.get_value("Resultado Bancario", r.name, "estado"), "Con diferencias")

    def test_marcar_revisado_solo_con_diferencias(self):
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        with self.assertRaises(frappe.ValidationError):     # todavía no hay nada que revisar
            api.marcar_revisado(r.name)
        r = aplicar.cargar_archivo(r.name, self._subir(self._csv(importe="99.00"), resultado=r.name))
        api.marcar_revisado(r.name)
        self.assertEqual(frappe.db.get_value("Resultado Bancario", r.name, "estado"), "Revisado")
        # Revisado no se pierde al volver a guardar: el visto bueno es de una persona.
        frappe.get_doc("Resultado Bancario", r.name).save()
        self.assertEqual(frappe.db.get_value("Resultado Bancario", r.name, "estado"), "Revisado")

    def test_la_respuesta_tiene_que_ser_un_archivo_privado(self):
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        publico = self._subir(self._csv(), nombre="respuesta-publica.csv", privado=0)
        with self.assertRaises(frappe.ValidationError):
            aplicar.cargar_archivo(r.name, publico)

    def test_un_archivo_ilegible_lo_dice_en_espanol(self):
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        file_url = self._subir(b"hola\n", nombre="basura.csv", resultado=r.name)
        with self.assertRaises(frappe.ValidationError):
            aplicar.cargar_archivo(r.name, file_url)
        self.assertEqual(frappe.db.get_value("Resultado Bancario", r.name, "origen"), "Captura manual")

    def test_cargar_el_archivo_de_ancho_fijo_de_exportacion(self):
        """El otro formato que da el banco: el mismo archivo que se le subió con los campos de
        respuesta pegados al final. Aquí es de OTRO lote a propósito, y eso se ve en las diferencias."""
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        file_url = self._subir(exportacion(), nombre="170926-0002-12.txt", resultado=r.name)
        r = aplicar.cargar_archivo(r.name, file_url)
        self.assertEqual((r.origen, r.estatus_archivo, r.autorizacion),
                         ("Archivo del portal", "30", "119938"))
        self.assertEqual([m.estatus for m in r.movimientos], ["3", "5"])
        self.assertEqual((r.num_aplicados, r.num_rechazados, r.total_archivo), (1, 1, 16540.10))
        self.assertEqual(r.estado, "Con diferencias")
        self.assertIn("2 movimientos", r.diferencias)

    def test_una_linea_que_no_corresponde_a_ninguna_transferencia(self):
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        r.movimientos[0].linea = 9
        r.save()
        self.assertEqual(r.estado, "Con diferencias")
        self.assertIn("9", r.diferencias)
        self.assertFalse(r.movimientos[0].transferencia_idx)

    def test_un_conteo_distinto_de_movimientos_es_una_diferencia(self):
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        r.append("movimientos", {"linea": 2, "cuenta": CLABE_12, "importe": 50, "estatus": "3"})
        r.save()
        self.assertEqual(r.estado, "Con diferencias")
        self.assertIn("2 movimientos", r.diferencias)

    def test_un_archivo_rechazado_por_el_banco_se_dice_en_las_diferencias(self):
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        r.estatus_archivo = "32"
        r.save()
        self.assertEqual(r.estado, "Con diferencias")
        self.assertIn("rechazó", r.diferencias)

    def test_la_guardia_no_deja_mover_el_estado_a_mano(self):
        """`estado`, `aplicado_el` y `aplicado_por` los escribe la aplicación de los pagos: a mano
        serían una factura dada por pagada sin que exista el pago."""
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        for campo, valor in (("estado", "Aplicado"), ("estado", "Revisado"),
                             ("aplicado_el", "2026-09-18 10:00:00"), ("aplicado_por", "Administrator")):
            with self.subTest(campo=campo, valor=valor):
                doc = frappe.get_doc("Resultado Bancario", r.name)
                doc.set(campo, valor)
                with self.assertRaisesRegex(frappe.ValidationError, "a mano"):
                    doc.save()

    def test_la_guardia_no_deja_escribir_la_accion_de_un_movimiento_a_mano(self):
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        r.movimientos[0].accion = "Pago creado"
        with self.assertRaisesRegex(frappe.ValidationError, "a mano"):
            r.save()

    def test_la_guardia_deja_pasar_lo_que_si_se_captura(self):
        """La guardia no puede estorbar el trabajo de todos los días: el estatus, la clave de rastreo
        y el motivo de una línea sin pago se capturan a mano y se guardan sin más."""
        r = aplicar.crear_resultado_desde_lote(self._lote_transmitido())
        r.movimientos[0].estatus = "5"
        r.movimientos[0].motivo = "CUENTA CANCELADA"
        r.save()
        self.assertEqual((r.num_rechazados, r.estado), (1, "Importado"))

    def test_solo_tesoreria_captura_el_resultado(self):
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        file_url = self._subir(self._csv(), resultado=r.name)
        frappe.set_user(REVISOR)
        for llamada in (lambda: api.crear_captura_manual(lote),
                        lambda: api.importar_respuesta(r.name, file_url),
                        lambda: api.marcar_revisado(r.name)):
            with self.assertRaises(frappe.PermissionError):
                llamada()

    def test_el_permiso_sobre_el_lote_tambien_cuenta(self):
        """El rol es global; las User Permissions por empresa sólo se aplican mirando el documento.
        Sin eso, quien tuviera el rol de Tesorería podía capturar el resultado del lote de otra
        empresa, que es decir qué facturas se dan por pagadas."""
        lote = self._lote_transmitido()
        r = aplicar.crear_resultado_desde_lote(lote)
        file_url = self._subir(self._csv(), resultado=r.name)
        self._solo_ve("Lote de Pago", "LOTE-QUE-NO-ES-ESTE")
        frappe.set_user(TESORERIA)
        for llamada in (lambda: api.crear_captura_manual(lote),
                        lambda: api.importar_respuesta(r.name, file_url)):
            with self.assertRaises(frappe.PermissionError):
                llamada()
        self.assertEqual(frappe.db.get_value("Resultado Bancario", r.name, "origen"), "Captura manual")

    def test_tesoreria_importa_y_recibe_el_resumen(self):
        """El contrato que consume el botón del formulario: un resumen para el msgprint."""
        lote = self._lote_transmitido()
        frappe.set_user(TESORERIA)
        nombre = api.crear_captura_manual(lote)
        file_url = self._subir(self._csv(estatus="5"), resultado=nombre)
        respuesta = api.importar_respuesta(nombre, file_url)
        self.assertEqual(respuesta["name"], nombre)
        self.assertIn("1", respuesta["resumen"])
        self.assertEqual(frappe.db.get_value("Resultado Bancario", nombre, "num_rechazados"), 1)
