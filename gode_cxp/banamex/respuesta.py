"""Lectura de lo que contesta BancaNet sobre un lote de pago.

Dos formas del mismo dato, porque el banco ofrece las dos y Tesorería usará la que tenga a mano:

1. **CSV/TXT delimitado** exportado de la pantalla del portal (separador `,`, `;` o tabulador, con
   encabezado en la primera fila). Las columnas se reconocen por nombre, sin acentos ni mayúsculas.
2. **Archivo de ancho fijo de EXPORTACIÓN del layout C**: el mismo archivo que se le subió al banco
   con campos de respuesta pegados al final de cada registro (`docs/banamex-formatos.md`, sección
   "Exportación").

No importa frappe: se puede probar y usar fuera del sitio. Los mensajes de error salen como
`RespuestaInvalida`; quien lo llame desde el escritorio (banamex/aplicar.py) los convierte en
`frappe.throw`.
"""
import csv
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from gode_cxp.pagos.tef import L1, L2, L3, L4, TefInvalido, leer_tef

# --- CSV exportado del portal ---------------------------------------------------------------------

ALIAS = {
    "linea": ("linea", "consecutivo", "no", "num", "numero", "registro"),
    "beneficiario": ("beneficiario", "nombre", "nombre del beneficiario"),
    "cuenta": ("cuenta", "clabe", "cuenta beneficiario", "cuenta destino"),
    "importe": ("importe", "monto", "cantidad"),
    "estatus": ("estatus", "estado", "status"),
    "motivo": ("motivo", "descripcion", "descripcion del estatus", "observaciones", "mensaje"),
    "clave_rastreo": ("clave de rastreo", "rastreo", "clave rastreo", "referencia spei"),
    "autorizacion": ("autorizacion", "numero de autorizacion", "no de autorizacion"),
}
# Lo que puede traer la columna de estatus. El banco usa 3 y 5 por movimiento y 30 / 32 por archivo.
ESTATUS = {"3": "3", "aplicado": "3", "aplicada": "3", "30": "3",
           "5": "5", "rechazado": "5", "rechazada": "5", "32": "5"}
# Lo que cabe en los campos del Resultado Bancario Movimiento.
LARGO_BENEFICIARIO, LARGO_CUENTA, LARGO_MOTIVO, LARGO_CLAVE = 55, 20, 140, 40

# --- archivo de ancho fijo de exportación ---------------------------------------------------------
# Largo de cada registro en la exportación (la ayuda de BancaNet los da; las POSICIONES de los campos
# agregados están DEDUCIDAS sumando longitudes porque esa página no las numera). Van todas juntas a
# propósito: cuando llegue el primer archivo real de respuesta se corrigen aquí y en ningún otro lado.
E1, E2, E3, E4 = 138, 87, 269, 52
# tipo de registro -> (largo en el archivo que se sube, largo en el que devuelve el banco)
LARGOS = {"1": (L1, E1), "2": (L2, E2), "3": (L3, E3), "4": (L4, E4)}
POS_ESTATUS_ARCHIVO = slice(124, 126)        # registro 1, posiciones 125-126
POS_AUTORIZACION_ARCHIVO = slice(126, 138)   # registro 1, posiciones 127-138
POS_AUTORIZACION = slice(217, 229)           # registro 3, posiciones 218-229
POS_ESTATUS = slice(229, 230)                # registro 3, posición 230
POS_ERROR_ORIGINADO = slice(230, 234)        # registro 3, posiciones 231-234 (blancos; no se usa)
POS_NUM_ERROR = slice(234, 238)              # registro 3, posiciones 235-238
POS_MENSAJE = slice(238, 269)                # registro 3, posiciones 239-269

APLICADO, CANCELADO, RECHAZADO = "30", "10", "32"
# Lo único que el banco pone en el estatus del archivo. Cualquier otra cosa ahí significa que los
# campos del layout no están donde los esperamos, y entonces TODO lo demás que leamos es inventado.
ESTATUS_ARCHIVO = (APLICADO, CANCELADO, RECHAZADO)
# Si el banco tumba el archivo COMPLETO no pagó nada: cada línea cuenta como rechazada y el motivo
# dice por qué, aunque la línea traiga un 3 (el estatus del archivo manda sobre el de la línea).
MOTIVO_ARCHIVO = {RECHAZADO: "archivo rechazado por el banco",
                  CANCELADO: "archivo cancelado por el banco"}
# Lo que se le dice a quien suba un archivo que no cuadra con el layout: es siempre el mismo problema.
NO_CUADRA = ("el archivo no cuadra con el layout de exportación (¿es el archivo que devolvió el "
             "banco, completo y sin editar?)")


class RespuestaInvalida(ValueError):
    pass


def _clave(texto):
    """Un encabezado de columna comparable: sin acentos, en minúsculas y con un solo espacio."""
    s = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos.lower().strip().strip('"'))


def _importe(texto, donde=""):
    """El importe de una línea. No se adivina nada: un importe en blanco no es cero (es un archivo
    que no entendimos) y una coma decimal al estilo europeo (`3.275,10`) no se convierte a la
    brava, porque quitarle las comas lo multiplicaría por cien."""
    donde = f" en {donde}" if donde else ""
    crudo = str(texto or "").replace("$", "").replace(" ", "").strip('"').strip()
    if not crudo:
        raise RespuestaInvalida(f"importe vacío{donde}")
    # Un separador de miles siempre agrupa de tres en tres: una coma con uno o dos dígitos detrás
    # sólo puede ser un decimal, y entonces el archivo no viene en el formato que esperamos.
    if re.search(r",\d{1,2}$", crudo):
        raise RespuestaInvalida(f"importe con coma decimal{donde}: {texto!r}; el archivo del banco "
                                "usa el punto como decimal, así que este hay que capturarlo a mano")
    try:
        return Decimal(crudo.replace(",", ""))
    except InvalidOperation:
        raise RespuestaInvalida(f"importe ilegible{donde}: {texto!r}")


def _sin_ceros(texto):
    """Un número de autorización legible: sin espacios y sin los ceros a la izquierda con que el
    banco rellena el campo. Todo en ceros (lo que devuelve en una transferencia rechazada) es vacío."""
    return (texto or "").strip().lstrip("0")


def _columnas(encabezado):
    columnas = {}
    for campo, alias in ALIAS.items():
        for i, c in enumerate(encabezado):
            if c in alias:
                columnas[campo] = i
                break
    if "importe" not in columnas or "estatus" not in columnas:
        raise RespuestaInvalida("el archivo necesita al menos las columnas Importe y Estatus")
    return columnas


def _leer_csv(datos):
    texto = datos.decode("utf-8-sig", errors="replace")
    try:
        dialecto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;\t")
    except (csv.Error, IndexError):
        raise RespuestaInvalida("no se reconoce el separador del archivo (se esperan ',', ';' o tabulador)")
    filas = list(csv.reader(io.StringIO(texto), dialecto))
    if len(filas) < 2:
        raise RespuestaInvalida("el archivo no tiene datos (sólo el encabezado)")
    columnas = _columnas([_clave(c) for c in filas[0]])
    movimientos = []
    for n, fila in enumerate(filas[1:], start=1):
        if not any(c.strip() for c in fila):
            continue

        def col(campo, fila=fila):
            i = columnas.get(campo)
            return fila[i].strip() if i is not None and i < len(fila) else ""

        estatus = ESTATUS.get(_clave(col("estatus")))
        if estatus is None:
            raise RespuestaInvalida(f"estatus desconocido en la fila {n}: {col('estatus')!r}")
        linea = col("linea")
        movimientos.append({
            # Sin columna de consecutivo, la línea es el orden del archivo: es como se cruza con las
            # transferencias del lote.
            "linea": int(linea) if linea.isdigit() else n,
            "beneficiario": col("beneficiario")[:LARGO_BENEFICIARIO],
            # El portal escribe la cuenta con espacios o guiones; al lote entró sólo con dígitos.
            "cuenta": re.sub(r"\D", "", col("cuenta"))[-LARGO_CUENTA:],
            "importe": _importe(col("importe"), f"la fila {n}"),
            "estatus": estatus,
            "motivo": col("motivo")[:LARGO_MOTIVO],
            "clave_rastreo": col("clave_rastreo")[:LARGO_CLAVE],
            "autorizacion": _sin_ceros(col("autorizacion")),
        })
    return {"estatus_archivo": None, "autorizacion": None, "movimientos": movimientos, "total_archivo": None}


def _motivo(extra):
    """Número de error + mensaje del banco. Un error en ceros y un mensaje en blanco no son motivo
    (es lo que trae una transferencia aplicada). El campo 'error originado' viene en blancos."""
    numero = extra[POS_NUM_ERROR].strip()
    mensaje = extra[POS_MENSAJE].strip()
    if not numero.strip("0"):
        numero = ""
    return " ".join(parte for parte in (numero, mensaje) if parte)


def _canario(extra, linea):
    """Que los campos de respuesta estén DONDE los esperamos, antes de creerles nada.

    Las posiciones de la exportación están deducidas sumando longitudes (la ayuda de BancaNet no las
    numera), así que un archivo corrido un solo carácter se leería igual de bien y con el estatus de
    otro campo: dar por pagada una transferencia que el banco rechazó. Dos campos delatan el
    corrimiento: 'error originado' viene siempre en blancos, y la autorización siempre en dígitos
    (en ceros cuando no pagó) o en blancos."""
    if extra[POS_ERROR_ORIGINADO].strip():
        raise RespuestaInvalida(
            f"la línea {linea} trae {extra[POS_ERROR_ORIGINADO]!r} donde el banco deja blancos: "
            f"{NO_CUADRA}")
    autorizacion = extra[POS_AUTORIZACION].strip()
    if autorizacion and not autorizacion.isdigit():
        raise RespuestaInvalida(
            f"la autorización de la línea {linea} dice {autorizacion!r} y no son dígitos: {NO_CUADRA}")


def _movimiento(t, extra, estatus_archivo):
    _canario(extra, t["linea"])
    crudo = extra[POS_ESTATUS].strip()
    estatus = ESTATUS.get(crudo)
    motivo = _motivo(extra)
    if estatus is None:
        if not crudo and estatus_archivo in MOTIVO_ARCHIVO:
            estatus, motivo = "5", motivo or MOTIVO_ARCHIVO[estatus_archivo]
        else:
            raise RespuestaInvalida(f"estatus desconocido en la línea {t['linea']}: {crudo!r}")
    return {
        "linea": t["linea"], "beneficiario": t["beneficiario"],
        # Naturaleza 12 trae la CLABE; la 06, sucursal + cuenta Banamex (igual que en el lote).
        "cuenta": (t.get("clabe") or f"{t.get('sucursal', '')}{t.get('cuenta', '')}")[-LARGO_CUENTA:],
        "importe": t["importe"], "estatus": estatus, "motivo": motivo[:LARGO_MOTIVO],
        # La exportación del layout C no trae clave de rastreo: eso sólo sale del portal o del
        # comprobante individual en PDF, así que aquí se queda vacía y Tesorería la captura si hace falta.
        "clave_rastreo": "", "autorizacion": _sin_ceros(extra[POS_AUTORIZACION]),
    }


def _leer_layout(datos):
    """El archivo de exportación: se parte en el cuerpo de importación (que valida `leer_tef`) y los
    campos de respuesta que el banco pegó al final de cada registro."""
    try:
        texto = datos.decode("ascii")
    except UnicodeDecodeError:
        raise RespuestaInvalida("el archivo de ancho fijo no es ASCII")
    # Cualquier fin de línea: el archivo del banco trae CRLF, pero pasa por el correo y por algún editor.
    lineas = [l for l in re.split(r"\r\n|\r|\n", texto) if l.strip()]
    importacion, respuesta = [], []
    for n, linea in enumerate(lineas, start=1):
        tipo = linea[:1]
        if tipo not in LARGOS:
            raise RespuestaInvalida(f"la línea {n} no empieza con un tipo de registro conocido ({tipo!r})")
        corto, largo = LARGOS[tipo]
        if len(linea) > largo:
            raise RespuestaInvalida(f"la línea {n} (registro {tipo}) mide {len(linea)}, esperaba {largo}")
        if tipo == "3" and len(linea) <= corto:
            raise RespuestaInvalida("el archivo no trae el estatus de cada transferencia: parece el "
                                    "archivo que se le subió al banco y no su respuesta")
        # Los espacios del final se pueden haber perdido en el camino: se rellenan a su largo.
        linea = linea.ljust(largo)
        importacion.append(linea[:corto])
        respuesta.append(linea)
    try:
        lote = leer_tef(("\r\n".join(importacion) + "\r\n").encode("ascii"))
    except TefInvalido as e:
        raise RespuestaInvalida(f"el cuerpo del archivo no es un TEF versión C válido: {e}")
    r1 = next(l for l in respuesta if l[:1] == "1")
    estatus_archivo = r1[POS_ESTATUS_ARCHIVO].strip() or None
    if estatus_archivo is not None and estatus_archivo not in ESTATUS_ARCHIVO:
        # El mismo canario que el de cada registro 3, un registro más arriba.
        raise RespuestaInvalida(f"el estatus del archivo dice {estatus_archivo!r} y sólo puede ser "
                                f"30 (aplicado), 32 (rechazado) o 10 (cancelado): {NO_CUADRA}")
    extras = [l for l in respuesta if l[:1] == "3"]
    movimientos = [_movimiento(t, extra, estatus_archivo)
                   for t, extra in zip(lote["transferencias"], extras)]
    if estatus_archivo in MOTIVO_ARCHIVO:
        # El banco tumbó el archivo entero: no pagó NADA. Un '3' por línea es una contradicción del
        # banco consigo mismo, y de las dos versiones la única segura es que no hubo pago.
        for m in movimientos:
            m["estatus"] = "5"
            m["motivo"] = m["motivo"] or MOTIVO_ARCHIVO[estatus_archivo]
    return {
        "estatus_archivo": estatus_archivo,
        "autorizacion": _sin_ceros(r1[POS_AUTORIZACION_ARCHIVO]) or None,
        "movimientos": movimientos,
        "total_archivo": lote["total"],
    }


def leer_respuesta(datos, nombre_archivo=""):
    """Lee la respuesta del banco (bytes) y devuelve
    `{"estatus_archivo", "autorizacion", "movimientos": [...], "total_archivo", "num_aplicados",
    "num_rechazados"}`.

    Qué lector se usa lo decide el CONTENIDO, no la extensión (`nombre_archivo` sólo sirve para los
    mensajes): un registro 1 del layout C empieza con "1" y el número de contrato, o sea 13 dígitos.
    """
    nombre = nombre_archivo or "el archivo del banco"
    if not datos or not datos.strip():
        raise RespuestaInvalida(f"{nombre} está vacío")
    cabeza = datos.lstrip()[:13]
    es_layout = cabeza[:1] == b"1" and len(cabeza) == 13 and cabeza.isdigit()
    r = _leer_layout(datos) if es_layout else _leer_csv(datos)
    if not r["movimientos"]:
        raise RespuestaInvalida(f"{nombre} no trae movimientos")
    r["num_aplicados"] = sum(1 for m in r["movimientos"] if m["estatus"] == "3")
    r["num_rechazados"] = sum(1 for m in r["movimientos"] if m["estatus"] == "5")
    return r
