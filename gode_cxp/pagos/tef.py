"""Archivo TEF Banamex versión C (BancaNet Empresarial), ancho fijo, tomado byte a byte de los archivos
aceptados por el banco el 17/09/2026. No importa frappe: se puede probar y usar fuera del sitio."""
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

FIN = b"\r\n"
L1, L2, L3, L4 = 124, 69, 217, 52
# Lotes por día que admite BancaNet Empresarial: 0001 a 0099.
MAX_SECUENCIAL = 99
# El punto está permitido porque aparece en un beneficiario del archivo que Banamex aceptó
# ('DISTRIBUIDORA,DE ALIMENTOS P.B. SA DE CV/', línea 7 de PAGOS2.txt). Ojo: la validación de
# pagos.cuentas_bancarias.validar_nombre_tef es más estricta y sí lo rechaza; aquí el criterio es
# poder reconstruir los archivos reales byte a byte.
# Los dos juegos se usan con fullmatch y SIN "$": con "$" y re.match, "ABC/\n" pasaría (el "$" casa
# antes del salto de línea final) y ese salto correría el archivo de ancho fijo completo.
PERMITIDOS = re.compile(r"[A-Z0-9 ,./]*")
# El concepto va como lo capturó Tesorería ("pago gode" es minúsculas en los archivos que el banco
# aceptó), pero sigue siendo ASCII imprimible: ni control, ni tabuladores, ni saltos de línea.
IMPRIMIBLE = re.compile(r"[ -~]*")


class TefInvalido(ValueError):
    pass


def _centavos(importe):
    try:
        return int((Decimal(str(importe)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ArithmeticError, ValueError, TypeError):
        raise TefInvalido(f"importe inválido: {importe!r}")


def _num(valor, largo, campo=None):
    try:
        s = str(int(valor))
    except (ValueError, TypeError):
        raise TefInvalido(f"{campo or 'número'}: {valor!r} no es un número entero")
    if len(s) > largo:
        raise TefInvalido(f"{valor} no cabe en {largo} dígitos")
    return s.zfill(largo)


def _txt(texto, largo, campo, minusculas=False):
    """Campo de texto de ancho fijo. `minusculas=True` es para los campos que llevan el concepto tal
    como lo capturó Tesorería; el resto va en mayúsculas, dígitos, espacio, coma, punto y diagonal."""
    texto = "" if texto is None else str(texto)
    if len(texto) > largo:
        raise TefInvalido(f"{campo}: '{texto}' pasa de {largo} caracteres")
    if not texto.isascii():
        raise TefInvalido(f"{campo}: '{texto}' no es ASCII (el archivo del banco es ASCII puro)")
    if not (IMPRIMIBLE if minusculas else PERMITIDOS).fullmatch(texto):
        permitido = "ASCII imprimible" if minusculas else "solo A-Z 0-9 , . / y espacio"
        raise TefInvalido(f"{campo}: {texto!r} tiene caracteres no permitidos ({permitido})")
    return texto.ljust(largo)


def _ddmmaa(fecha):
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    if not isinstance(fecha, date):
        raise TefInvalido(f"fecha inválida: {fecha!r} (se espera una fecha, no texto)")
    return fecha.strftime("%d%m%y")


def nombre_archivo(fecha, secuencial, naturaleza):
    return f"{_ddmmaa(fecha)}-{int(secuencial):04d}-{naturaleza}.txt"


def _validar_lote(lote):
    if lote.get("naturaleza") not in ("06", "12"):
        raise TefInvalido("naturaleza debe ser 06 o 12")
    if not lote.get("transferencias"):
        raise TefInvalido("el lote no tiene transferencias")
    # Los datos que faltan se cachan aquí y no a media construcción del archivo: quien llama sólo
    # tiene que atrapar TefInvalido, nunca un KeyError ni un decimal.InvalidOperation.
    for campo in ("fecha", "empresa", "concepto"):
        if not lote.get(campo):
            raise TefInvalido(f"falta {campo} en el lote")
    _ddmmaa(lote["fecha"])
    try:
        secuencial = int(lote.get("secuencial") or 0)
    except (ValueError, TypeError):
        raise TefInvalido(f"secuencial inválido: {lote.get('secuencial')!r}")
    # El campo del archivo tiene 4 posiciones, pero BancaNet Empresarial sólo admite los lotes 0001
    # a 0099 de cada día: un 0100 lo rechaza el banco completo.
    if not 1 <= secuencial <= MAX_SECUENCIAL:
        raise TefInvalido(f"secuencial fuera de rango (1-{MAX_SECUENCIAL}: el banco sólo admite "
                          f"0001 a 00{MAX_SECUENCIAL} por día)")
    if not re.fullmatch(r"\d{1,12}", str(lote.get("contrato", ""))):
        raise TefInvalido("contrato inválido")
    if not re.fullmatch(r"\d{4}", str(lote.get("sucursal_cargo", ""))) or not re.fullmatch(r"\d{1,20}", str(lote.get("cuenta_cargo", ""))):
        raise TefInvalido("sucursal/cuenta de cargo inválidas")
    if not re.fullmatch(r"\d{7}", str(lote.get("referencia_numerica", ""))):
        raise TefInvalido("referencia numérica debe tener 7 dígitos")
    for t in lote["transferencias"]:
        if not str(t.get("beneficiario") or "").strip():
            raise TefInvalido("una transferencia del lote no trae beneficiario")
        if _centavos(t.get("importe", 0)) <= 0:
            raise TefInvalido(f"importe inválido para {t.get('beneficiario')}")
        if lote["naturaleza"] == "12" and not re.fullmatch(r"\d{18}", str(t.get("clabe", ""))):
            raise TefInvalido(f"CLABE inválida para {t.get('beneficiario')}")
        if lote["naturaleza"] == "06" and (not re.fullmatch(r"\d{4}", str(t.get("sucursal", ""))) or not re.fullmatch(r"\d{7}", str(t.get("cuenta", "")))):
            raise TefInvalido(f"sucursal/cuenta Banamex inválidas para {t.get('beneficiario')}")


def _registro_3(lote, t):
    nat, concepto = lote["naturaleza"], lote["concepto"]
    if nat == "06":
        # Dentro de Banamex la cuenta de 20 va como 9 ceros + sucursal (4) + cuenta (7), la referencia
        # lleva la fecha y el concepto viaja en el campo de instrucciones.
        cuenta_20 = "0" * 9 + t["sucursal"] + t["cuenta"]
        referencia = "0000" + _ddmmaa(lote["fecha"])
        instrucciones, clave_banco, ref_num = concepto, "0000", "0000000"
    else:
        # Interbancario: CLABE de 18 con dos ceros al frente, concepto en la referencia, sin
        # instrucciones, y la clave del banco receptor sale de los tres primeros dígitos de la CLABE.
        cuenta_20 = "00" + t["clabe"]
        referencia, instrucciones = concepto, ""
        clave_banco, ref_num = "0" + t["clabe"][:3], lote["referencia_numerica"]
    # referencia e instrucciones llevan el concepto tal cual (minúsculas incluidas) o la fecha; por
    # eso van con minusculas=True, y con su propio nombre para que el mensaje de error se entienda.
    linea = ("3" + "0" + "001" + _num(_centavos(t["importe"]), 18, "importe") + "01" + cuenta_20
             + _txt(referencia, 40, "referencia", minusculas=True) + _txt(t["beneficiario"], 55, "beneficiario")
             + _txt(instrucciones, 40, "instrucciones", minusculas=True) + " " * 24 + clave_banco + ref_num + "00")
    if len(linea) != L3:
        raise TefInvalido(f"registro 3 mide {len(linea)}, esperaba {L3}")
    return linea


def generar_tef(lote):
    """bytes del archivo listo para BancaNet (ASCII, CRLF en todas las líneas)."""
    _validar_lote(lote)
    total = sum(_centavos(t["importe"]) for t in lote["transferencias"])
    r1 = ("1" + _num(lote["contrato"], 12, "contrato") + _ddmmaa(lote["fecha"]) + _num(lote["secuencial"], 4, "secuencial")
          + _txt(lote["empresa"], 36, "empresa") + _txt(lote["concepto"], 20, "concepto", minusculas=True)
          + lote["naturaleza"] + " " * 40 + "C00")
    r2 = ("2" + "1" + "001" + _num(total, 18, "total") + "01" + lote["sucursal_cargo"]
          + _num(lote["cuenta_cargo"], 20, "cuenta de cargo") + " " * 20)
    r3 = [_registro_3(lote, t) for t in lote["transferencias"]]
    r4 = "4" + "001" + _num(len(r3), 6) + _num(total, 18, "total") + "000001" + _num(total, 18, "total")
    for linea, largo in ((r1, L1), (r2, L2), (r4, L4)):
        if len(linea) != largo:
            raise TefInvalido(f"registro {linea[0]} mide {len(linea)}, esperaba {largo}")
    return FIN.join(l.encode("ascii") for l in [r1, r2, *r3, r4]) + FIN


def leer_tef(datos):
    """Lee un archivo versión C y devuelve el mismo dict que acepta generar_tef (más total, num_abonos y detalle por línea)."""
    if not datos.endswith(FIN):
        raise TefInvalido("el archivo no termina en CRLF")
    lineas = datos[:-2].split(FIN)
    try:
        texto = [l.decode("ascii") for l in lineas]
    except UnicodeDecodeError:
        raise TefInvalido("el archivo no es ASCII")
    if len(texto) < 4 or texto[0][:1] != "1" or texto[1][:1] != "2" or texto[-1][:1] != "4":
        raise TefInvalido("estructura inesperada: se esperan registros 1, 2, 3… y 4")
    r1, r2, r4, r3s = texto[0], texto[1], texto[-1], texto[2:-1]
    # Entre el registro 2 y el 4 sólo van transferencias: cualquier otra cosa (dos archivos pegados,
    # un registro 2 repetido) se leería como una transferencia con basura en todos los campos.
    for i, l in enumerate(r3s, start=3):
        if l[:1] != "3":
            raise TefInvalido(f"la línea {i} debería ser un registro 3 y empieza con {l[:1]!r}")
    for linea, largo in ((r1, L1), (r2, L2), (r4, L4), *[(l, L3) for l in r3s]):
        if len(linea) != largo:
            raise TefInvalido(f"registro {linea[:1]} mide {len(linea)}, esperaba {largo}")
    nat = r1[79:81]
    lote = {
        "contrato": r1[1:13], "fecha": datetime.strptime(r1[13:19], "%d%m%y").date(), "secuencial": int(r1[19:23]),
        "empresa": r1[23:59].rstrip(), "concepto": r1[59:79].rstrip(), "naturaleza": nat,
        "sucursal_cargo": r2[25:29], "cuenta_cargo": r2[29:49].lstrip("0") or "0",
        "total": Decimal(int(r4[10:28])) / 100, "num_abonos": int(r4[4:10]), "transferencias": [],
    }
    # En naturaleza 06 el archivo no trae la referencia numérica (va en ceros): se reconstruye de la fecha.
    lote["referencia_numerica"] = "0" + _ddmmaa(lote["fecha"])
    for i, l in enumerate(r3s, start=1):
        t = {"linea": i, "importe": Decimal(int(l[5:23])) / 100, "cuenta_20": l[25:45], "referencia": l[45:85].rstrip(),
             "beneficiario": l[85:140].rstrip(), "instrucciones": l[140:180].rstrip(), "clave_banco": l[204:208],
             "referencia_numerica": l[208:215], "plazo": l[215:217]}
        if nat == "12":
            t["clabe"] = l[27:45]
            lote["referencia_numerica"] = t["referencia_numerica"]
        else:
            t["sucursal"], t["cuenta"] = l[34:38], l[38:45]
        lote["transferencias"].append(t)
    if lote["num_abonos"] != len(r3s) or sum(_centavos(t["importe"]) for t in lote["transferencias"]) != _centavos(lote["total"]):
        raise TefInvalido("el registro 4 no cuadra con las transferencias")
    return lote
