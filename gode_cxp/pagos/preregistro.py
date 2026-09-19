"""Pre-registro (alta) de las cuentas de los proveedores en BancaNet.

Banamex no deja pagar a una cuenta que no está dada de alta en el contrato, y el alta masiva se hace
con una plantilla de Excel CON MACROS que entrega el banco (`Plantilla_Pre-registro de cuentas`, hoja
`LOIncorOP`): esa macro arma el archivo `abcMasivo…txt` que se sube. La app NO genera ese TXT —de él
sólo hay una muestra, de cuentas Banamex de cheques de personas físicas, así que no se conocen las
posiciones de una CLABE interbancaria ni de una persona moral—: produce las FILAS listas para pegar
en la plantilla del banco y lee la RESPUESTA que BancaNet devuelve.

Formatos: frappe-hr-ops/docs/banamex-formatos.md.
"""
import io

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from gode_cxp.pagos.cuentas_bancarias import (
    ENVIADA_AL_BANCO,
    RECHAZADA,
    REGISTRADA,
    SIN_PREREGISTRO,
    transliterar,
)

# --- la plantilla del banco ----------------------------------------------------------------------

HOJA = "LOIncorOP"
# EXACTAMENTE las columnas de la hoja LOIncorOP, en su orden: lo que se genera se pega en la
# plantilla del banco, así que ni el orden ni los nombres son nuestros.
COLUMNAS = ("MOVIMIENTO", "BANCO", "SUCURSAL", "TIPO DE CUENTA", "NUMERO DE CUENTA",
            "TIPO DE PERSONA", "PERIODO", "IMPORTE MAXIMO", "BENEFICIARIO", "ALIAS", "RFC",
            "CELULAR", "EMAIL")
MOVIMIENTO_ALTA = "ALTA"
TIPO_DE_CUENTA = {"06": "CHEQUES", "12": "CLABE INTERBANCARIA"}
TIPO_DE_PERSONA = {"Física": "PERSONA FISICA", "Moral": "PERSONA MORAL"}
LARGO_ALIAS = 20
PERIODO_DEFAULT = "DIARIO"
IMPORTE_MAXIMO_DEFAULT = 500000
# Lo que cabe en Bank Account.preregistro_respuesta.
LARGO_RESPUESTA = 140
MARCADA_A_MANO = "registrada previamente (marcada a mano por {0})"

# Catálogo de claves de banco de la CLABE, transcrito del que traen las plantillas de Banamex
# (tabla completa en docs/banamex-formatos.md). La columna BANCO de la plantilla quiere el "nombre
# corto" de este catálogo. Los que de verdad usan los proveedores de GODE son los primeros; el resto
# está para no dejar huecos (algunos nombres salieron de un PDF y pueden traer una palabra de más).
BANCOS_CLABE = {
    "002": "BANAMEX", "006": "BANCOMEXT", "009": "BANOBRAS", "012": "BBVA", "014": "SANTANDER",
    "019": "BANJERCITO", "021": "HSBC", "030": "BAJIO", "032": "IXE", "036": "INBURSA",
    "037": "INTERACCIONES", "042": "MIFEL", "044": "SCOTIABANK", "058": "BANREGIO", "059": "INVEX",
    "060": "BANSI", "062": "AFIRME", "072": "BANORTE", "102": "THE ROYAL BANK",
    "103": "AMERICAN EXPRESS", "106": "BAMSA", "108": "TOKYO", "110": "JP MORGAN", "112": "BMONEX",
    "113": "VE POR MAS", "116": "ING", "124": "DEUTSCHE", "126": "CREDIT SUISSE", "127": "AZTECA",
    "128": "AUTOFIN", "129": "BARCLAYS", "130": "COMPARTAMOS", "131": "BANCO FAMSA",
    "132": "BMULTIVA", "133": "ACTINVER", "134": "WAL-MART", "135": "NAFIN", "136": "INTERBANCO",
    "137": "BANCOPPEL", "138": "ABC CAPITAL", "139": "UBS BANK", "140": "CONSUBANCO",
    "141": "VOLKSWAGEN", "143": "CIBANCO", "145": "BBASE", "166": "BANSEFI",
    "168": "HIPOTECARIA FEDERAL", "600": "MONEXCB", "601": "GBM", "602": "MASARI", "605": "VALUE",
    "606": "ESTRUCTURADORES", "607": "TIBER", "608": "VECTOR", "610": "B&B", "614": "ACCIVAL",
    "615": "MERRILL LYNCH", "616": "FINAMEX", "617": "VALMEX", "618": "UNICA", "619": "MAPFRE",
    "620": "PROFUTURO", "621": "CB ACTINVER", "622": "OACTIN", "623": "SKANDIA",
    "626": "CBDEUTSCHE", "627": "ZURICH", "628": "ZURICHVI", "629": "SU CASITA",
    "630": "CB INTERCAM", "631": "CI BOLSA", "632": "BULLTICK CB", "633": "STERLING",
    "634": "FINCOMUN", "636": "HDI SEGUROS", "637": "ORDER", "638": "AKALA", "640": "CB JPMORGAN",
    "642": "REFORMA", "646": "STP", "647": "TELECOMM", "648": "EVERCORE", "649": "SKANDIA",
    "651": "SEGMTY", "652": "ASEA", "653": "KUSPIT", "655": "SOFIEXPRESS", "656": "UNAGRA",
    "659": "EMPRESARIALES", "670": "LIBERTAD", "684": "TRANSFER", "722": "MERCADO PAGO W",
    "901": "CLS", "902": "INDEVAL", "999": "N/A",
}

# --- la respuesta del banco ----------------------------------------------------------------------

# Posiciones del archivo `abcMasivo…txt` de respuesta, TODAS sacadas de la única muestra real que hay
# (68 altas del 17/09/2026, cuentas Banamex de cheques) porque el banco no entregó el layout. Se
# dejan juntas a propósito: si aparece otra muestra y algo no cuadra, se corrige aquí y nada más.
# Encabezado (115): '0001' + fecha DD/MM/AAAA + hora HH:MM + contrato (12) + …
# Detalle (325): 'A' + banco (2-5) + tipo de cuenta (6-7) + un número fijo del contrato (8-19) +
#   cuenta a 20 (20-39) + beneficiario (40-94) + alias (95-114) + moneda (115-117) + … +
#   código de 4 dígitos (266-269) + mensaje (270-325).
PREFIJO_ALTA = "A"
LARGO_REGISTRO = 325
LARGO_ENCABEZADO = 115
POS_BANCO = slice(1, 5)
POS_TIPO_CUENTA = slice(5, 7)
POS_CUENTA = slice(19, 39)
POS_BENEFICIARIO = slice(39, 94)
POS_CODIGO = slice(265, 269)
POS_MENSAJE = slice(269, 325)
CODIGO_APLICADA = "0000"
# Tipo de cuenta del banco dentro del registro: 01 = cheques. La muestra sólo trae cheques; para
# todo lo demás se asume CLABE, que es la otra forma en que un proveedor puede cobrar.
TIPO_CHEQUES = "01"
# Dentro de la cuenta a 20, la regla es del propio banco (layout D de docs/banamex-formatos.md):
# cheques = 9 ceros + sucursal (4) + cuenta (7); CLABE = 2 ceros + los 18 de la CLABE.
POS_SUCURSAL_EN_CUENTA = slice(9, 13)
POS_CUENTA_EN_CUENTA = slice(13, 20)
POS_CLABE_EN_CUENTA = slice(2, 20)

CAMPOS_CUENTA = ("name", "party", "clabe", "tipo_pago_tef", "sucursal_banamex", "cuenta_banamex",
                 "nombre_tef", "importe_maximo_banco")


def banco_de_clabe(clabe):
    """Nombre corto del banco según los 3 primeros dígitos de la CLABE. Si la clave no está en el
    catálogo va el código tal cual: el banco rechazará la fila y se verá por qué, que es mejor que
    mandar la columna vacía."""
    clave = (clabe or "")[:3]
    return BANCOS_CLABE.get(clave, clave)


def _configuracion():
    conf = frappe.get_doc("Configuracion CxP")
    return (conf.periodo_preregistro or PERIODO_DEFAULT,
            flt(conf.importe_maximo_preregistro) or IMPORTE_MAXIMO_DEFAULT)


def cuentas_por_registrar(cuentas=None):
    """Las cuentas de proveedor que hay que dar de alta: activas, con CLABE y verificadas por
    Tesorería. Sin lista, las que siguen en 'Sin registrar'; con lista, exactamente ésas (así se
    puede volver a mandar una que el banco rechazó)."""
    filtros = {"party_type": "Supplier", "disabled": 0, "verificada": 1, "clabe": ["!=", ""]}
    if cuentas:
        filtros["name"] = ["in", cuentas]
    else:
        filtros["estado_preregistro"] = SIN_PREREGISTRO
    encontradas = frappe.get_all("Bank Account", filters=filtros, fields=list(CAMPOS_CUENTA),
                                 order_by="name")
    if cuentas:
        faltan = sorted(set(cuentas) - {c.name for c in encontradas})
        if faltan:
            frappe.throw(_("Estas cuentas no se pueden pre-registrar en el banco (tienen que ser de "
                           "proveedor, activas, con CLABE y verificadas por Tesorería): {0}")
                         .format(", ".join(faltan)))
    return encontradas


def filas_para_plantilla(cuentas=None):
    """Una fila (dict con las columnas de la hoja LOIncorOP) por cuenta a dar de alta."""
    periodo, importe_conf = _configuracion()
    filas = []
    for c in cuentas_por_registrar(cuentas):
        p = frappe.db.get_value("Supplier", c.party,
                                ["supplier_name", "tipo_persona", "tax_id", "correo_avisos"], as_dict=True)
        if c.tipo_pago_tef not in TIPO_DE_CUENTA:
            frappe.throw(_("La cuenta {0} no tiene naturaleza TEF: vuelve a guardar la CLABE.").format(c.name))
        if p.tipo_persona not in TIPO_DE_PERSONA:
            frappe.throw(_("El proveedor {0} no tiene tipo de persona (física o moral) y el banco lo "
                           "exige para dar de alta la cuenta.").format(c.party))
        banamex = c.tipo_pago_tef == "06"
        filas.append({
            "MOVIMIENTO": MOVIMIENTO_ALTA,
            "BANCO": banco_de_clabe(c.clabe),
            # La sucursal sólo existe dentro de Banamex; en un interbancario la columna va vacía.
            "SUCURSAL": (c.sucursal_banamex or "") if banamex else "",
            "TIPO DE CUENTA": TIPO_DE_CUENTA[c.tipo_pago_tef],
            "NUMERO DE CUENTA": (c.cuenta_banamex or "") if banamex else c.clabe,
            "TIPO DE PERSONA": TIPO_DE_PERSONA[p.tipo_persona],
            "PERIODO": periodo,
            # El banco pide el importe máximo en pesos enteros.
            "IMPORTE MAXIMO": int(flt(c.importe_maximo_banco) or importe_conf),
            "BENEFICIARIO": c.nombre_tef,
            "ALIAS": transliterar(p.supplier_name)[:LARGO_ALIAS],
            "RFC": p.tax_id or "",
            # El banco manda el aviso del alta a este celular: no tenemos celulares de proveedor.
            "CELULAR": "",
            "EMAIL": p.correo_avisos or "",
        })
    return filas


def _libro(filas):
    """El XLSX de una sola hoja `LOIncorOP` con los encabezados y las filas. No es la plantilla del
    banco (esa lleva macros y validaciones): es de dónde se copian las filas."""
    from openpyxl import Workbook

    libro = Workbook()
    hoja = libro.active
    hoja.title = HOJA
    hoja.append(list(COLUMNAS))
    for fila in filas:
        hoja.append([fila[columna] for columna in COLUMNAS])
    flujo = io.BytesIO()
    libro.save(flujo)
    return flujo.getvalue()


def descargar_preregistro(cuentas=None):
    """Arma el XLSX con las filas del alta, lo guarda como File privado y deja esas cuentas en
    'Enviada al banco'. Devuelve {"file_url", "nombre", "cuentas"}."""
    if isinstance(cuentas, str):
        cuentas = frappe.parse_json(cuentas)
    # Los nombres se resuelven primero: `filas_para_plantilla` devuelve sólo las columnas del banco
    # y hace falta saber a qué cuentas hay que ponerles la fecha de envío.
    nombres = [c.name for c in cuentas_por_registrar(cuentas)]
    if not nombres:
        frappe.throw(_("No hay cuentas de proveedor por pre-registrar en BancaNet (verificadas y en "
                       "'Sin registrar')."))
    filas = filas_para_plantilla(nombres)
    nombre = f"preregistro-{now_datetime().strftime('%Y%m%d-%H%M')}.xlsx"
    archivo = frappe.get_doc({"doctype": "File", "file_name": nombre, "content": _libro(filas),
                              "is_private": 1}).insert(ignore_permissions=True)
    enviado = now_datetime()
    for name in nombres:
        frappe.db.set_value("Bank Account", name, {"estado_preregistro": ENVIADA_AL_BANCO,
                                                   "preregistro_enviado_el": enviado})
    return {"file_url": archivo.file_url, "nombre": nombre, "cuentas": len(nombres)}


def leer_respuesta_preregistro(datos):
    """Lee el `abcMasivo…txt` que devuelve BancaNet y saca un dict por registro de alta.

    Tolera líneas más cortas de lo esperado: si no llegan a la posición del código o del mensaje,
    esos dos campos salen vacíos en lugar de tirar la carga entera (un archivo a medio bajar no
    puede impedir aplicar lo que sí se entiende)."""
    texto = datos.decode("latin-1") if isinstance(datos, bytes) else datos
    registros = []
    for linea in texto.replace("\r\n", "\n").split("\n"):
        # El encabezado empieza con '0001' y las líneas vacías del final no son registros.
        if not linea.startswith(PREFIJO_ALTA):
            continue
        cuenta = linea[POS_CUENTA]
        tipo = linea[POS_TIPO_CUENTA]
        cheques = tipo == TIPO_CHEQUES
        registros.append({
            "banco": linea[POS_BANCO],
            "tipo_cuenta": tipo,
            "cuenta": cuenta,
            "sucursal": cuenta[POS_SUCURSAL_EN_CUENTA] if cheques else None,
            "cuenta_banamex": cuenta[POS_CUENTA_EN_CUENTA] if cheques else None,
            "clabe": None if cheques else cuenta[POS_CLABE_EN_CUENTA],
            "beneficiario": linea[POS_BENEFICIARIO].strip(),
            "codigo": linea[POS_CODIGO].strip(),
            "mensaje": linea[POS_MENSAJE].strip(),
        })
    return registros


def _cuenta_del_registro(registro):
    """La Bank Account que el banco contestó: por CLABE si es interbancaria, y por sucursal + cuenta
    si es de Banamex (que es como viaja en el archivo, sin la CLABE)."""
    filtros = {"party_type": "Supplier"}
    if registro["clabe"]:
        filtros["clabe"] = registro["clabe"]
    else:
        filtros["sucursal_banamex"] = registro["sucursal"]
        filtros["cuenta_banamex"] = registro["cuenta_banamex"]
    return frappe.db.get_value("Bank Account", filtros, "name")


def _contenido(file_url):
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not name:
        frappe.throw(_("No se encontró el archivo {0} en el sistema.").format(file_url))
    return frappe.get_doc("File", name).get_content()


def aplicar_respuesta_preregistro(file_url):
    """Cruza la respuesta del banco con las cuentas: código 0000 = Registrada, cualquier otro =
    Rechazada, con el código y el mensaje del banco guardados. Aplicar dos veces el mismo archivo
    deja exactamente lo mismo (se escribe el estado que dice el banco, no se acumula nada)."""
    registros = leer_respuesta_preregistro(_contenido(file_url))
    if not registros:
        frappe.throw(_("El archivo no trae ningún alta de cuenta (se esperan líneas que empiecen "
                       "con '{0}').").format(PREFIJO_ALTA))
    resultado = {"registradas": 0, "rechazadas": 0, "sin_coincidencia": []}
    for r in registros:
        cuenta = _cuenta_del_registro(r)
        if not cuenta:
            resultado["sin_coincidencia"].append(r["cuenta"])
            continue
        aplicada = r["codigo"] == CODIGO_APLICADA
        respuesta = f"{r['codigo']} {r['mensaje']}".strip()
        frappe.db.set_value("Bank Account", cuenta,
                            {"estado_preregistro": REGISTRADA if aplicada else RECHAZADA,
                             "preregistro_respuesta": respuesta[:LARGO_RESPUESTA]})
        resultado["registradas" if aplicada else "rechazadas"] += 1
    return resultado


def marcar_registrada(cuentas):
    """Para los proveedores que ya estaban dados de alta en BancaNet desde antes de este sistema:
    Tesorería lo declara a mano y queda escrito quién lo hizo."""
    if isinstance(cuentas, str):
        try:
            cuentas = frappe.parse_json(cuentas)
        except (ValueError, TypeError):
            cuentas = [cuentas]          # llegó un solo nombre, no una lista JSON
    if isinstance(cuentas, str):
        cuentas = [cuentas]              # llegó un JSON con un solo texto
    if not cuentas:
        frappe.throw(_("No se eligió ninguna cuenta bancaria."))
    respuesta = MARCADA_A_MANO.format(frappe.session.user)[:LARGO_RESPUESTA]
    for name in cuentas:
        c = frappe.db.get_value("Bank Account", name, ["party_type", "clabe", "verificada"], as_dict=True)
        if not c or c.party_type != "Supplier" or not c.clabe:
            frappe.throw(_("La cuenta {0} no es una cuenta de proveedor con CLABE.").format(name))
        if not c.verificada:
            frappe.throw(_("La cuenta {0} no está verificada por Tesorería: verifícala antes de "
                           "darla por registrada en el banco.").format(name))
        frappe.db.set_value("Bank Account", name, {"estado_preregistro": REGISTRADA,
                                                   "preregistro_respuesta": respuesta})
    return {"registradas": len(cuentas)}


def exigir_preregistro():
    """¿El lote exige que la cuenta esté dada de alta en el banco? (Configuración CxP)."""
    return bool(frappe.db.get_single_value("Configuracion CxP", "exigir_preregistro"))
