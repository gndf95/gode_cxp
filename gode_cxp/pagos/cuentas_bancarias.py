"""Cuentas bancarias de proveedor para el archivo TEF: CLABE, naturaleza, nombre del beneficiario y verificación."""
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import now_datetime

BANCO_BANAMEX = "002"          # código de banco dentro de la CLABE
PESOS_CLABE = (3, 7, 1)        # ponderación cíclica del dígito verificador (Banxico)
LARGO_NOMBRE_TEF = 55
# Juego de caracteres que Banamex aceptó en el beneficiario el 17/09/2026. El punto entra porque uno
# de los archivos que el banco tomó llevaba "DISTRIBUIDORA,DE ALIMENTOS P.B. SA DE CV/": una cuenta
# capturada a mano con puntos es válida, aunque lo que genera la app vaya sin ellos (transliterar los
# quita). Se usa con fullmatch, nunca con "$": "$" deja pasar un salto de línea al final, y un salto
# de línea dentro de un registro correría el archivo de ancho fijo completo.
PERMITIDOS_TEF = re.compile(r"[A-Z0-9 ,./]*")
# Campos que viajan al banco: si cambia uno, la verificación de Tesorería deja de valer. party y
# party_type están porque repuntar la cuenta a otro proveedor manda el dinero a otra parte con la
# misma CLABE y el mismo beneficiario verificados.
CAMPOS_AL_BANCO = ("party_type", "party", "clabe", "sucursal_banamex", "cuenta_banamex", "nombre_tef")
ROLES_VERIFICAN = ("CxP Tesoreria", "System Manager")


def validar_clabe(clabe):
    """True si son 18 dígitos y el dígito verificador cuadra (algoritmo de Banxico: pesos 3, 7 y 1
    cíclicos sobre los primeros 17 dígitos, cada producto módulo 10)."""
    clabe = (clabe or "").strip()
    if not re.fullmatch(r"\d{18}", clabe):
        return False
    suma = sum((int(d) * PESOS_CLABE[i % 3]) % 10 for i, d in enumerate(clabe[:17]))
    return (10 - suma % 10) % 10 == int(clabe[17])


def naturaleza_por_clabe(clabe):
    """06 = Banamex a Banamex (mismo banco); 12 = interbancario por SPEI."""
    return "06" if (clabe or "")[:3] == BANCO_BANAMEX else "12"


def transliterar(texto):
    """Mayúsculas ASCII sin acentos ni puntos; conserva letras, dígitos, espacio, coma y diagonal."""
    s = unicodedata.normalize("NFKD", texto or "")
    # NFKD ya parte la Ñ en "N" + tilde combinante, así que quitar los combinantes la deja en N.
    s = "".join(c for c in s if not unicodedata.combining(c)).upper().replace(".", "")
    s = "".join(c if PERMITIDOS_TEF.fullmatch(c) else " " for c in s)
    return re.sub(r"\s+", " ", s).strip()


def _parte(texto):
    """Un trozo del nombre listo para meterlo en la estructura: sin comas ni diagonales, que son los
    separadores del formato y dentro de un apellido lo romperían."""
    return re.sub(r"\s+", " ", transliterar(texto).replace(",", " ").replace("/", " ")).strip()


def nombre_tef_para(tipo_persona, nombre_pila, apellido_paterno, apellido_materno, razon_social):
    """Física: NOMBRES,PATERNO/MATERNO · Moral: PRIMERA,RESTO/ (formato aceptado por Banamex el
    17/09/2026). El recorte a 55 se hace sobre el cuerpo ANTES de armar la estructura: recortar el
    resultado ya armado se comería la diagonal final y el banco rechazaría el registro."""
    cuerpo = LARGO_NOMBRE_TEF - 2       # 55 menos los dos separadores (la coma y la diagonal)
    if tipo_persona == "Física" and (nombre_pila or apellido_paterno):
        nombres, paterno, materno = (_parte(t) for t in (nombre_pila, apellido_paterno, apellido_materno))
        # Se recorta lo menos importante primero: el materno, luego el paterno, luego los nombres.
        materno = materno[:max(0, cuerpo - len(nombres) - len(paterno))].rstrip()
        paterno = paterno[:max(0, cuerpo - len(nombres))].rstrip()
        nombres = nombres[:cuerpo].rstrip()
        return f"{nombres},{paterno}/{materno}"
    palabras = _parte(razon_social or nombre_pila).split()
    if not palabras:
        return ""
    primera = palabras[0][:cuerpo]
    resto = " ".join(palabras[1:])[:cuerpo - len(primera)].rstrip()
    return f"{primera},{resto}/"


def validar_nombre_tef(nombre):
    """None si es válido; si no, el motivo en español.

    La regla es la del banco, no una más estricta: Banamex tomó morales SIN coma
    ("MARINTER SA DE CV/") y beneficiarios con punto ("...P.B. SA DE CV/"), así que aquí se admiten.
    Si la app fuera más exigente que el banco, el beneficiario que guarda la app no coincidiría con
    el del histórico de septiembre de 2026."""
    nombre = nombre or ""
    if not nombre.strip():
        return "el beneficiario está vacío"
    if len(nombre) > LARGO_NOMBRE_TEF:
        return f"el beneficiario pasa de {LARGO_NOMBRE_TEF} caracteres"
    if not PERMITIDOS_TEF.fullmatch(nombre):
        return "el beneficiario solo admite mayúsculas sin acentos, dígitos, espacio, coma, punto y diagonal"
    if nombre.count(",") > 1:
        return "el beneficiario admite cuando más una coma (NOMBRES,PATERNO/MATERNO o PRIMERA,RESTO/)"
    if nombre.count("/") != 1:
        return "el beneficiario debe llevar exactamente una diagonal"
    return None


def validar_cuenta_bancaria(doc, method=None):
    """Hook Bank Account.validate: solo actúa en cuentas de proveedor."""
    if doc.party_type != "Supplier" or not doc.party:
        return
    doc.clabe = (doc.clabe or "").strip()
    if doc.clabe:
        if not validar_clabe(doc.clabe):
            frappe.throw(_("La CLABE {0} no es válida (18 dígitos con dígito verificador).").format(doc.clabe))
        doc.tipo_pago_tef = naturaleza_por_clabe(doc.clabe)
        if doc.tipo_pago_tef == "06":
            if not re.fullmatch(r"\d{4}", doc.sucursal_banamex or "") or not re.fullmatch(r"\d{7}", doc.cuenta_banamex or ""):
                frappe.throw(_("Cuenta Banamex: hacen falta sucursal (4 dígitos) y cuenta (7 dígitos) para la naturaleza 06."))
        else:
            # Sucursal y cuenta solo existen dentro de Banamex: en un interbancario estorban.
            doc.sucursal_banamex = doc.cuenta_banamex = None
    else:
        # Sin CLABE no hay TEF: dejar los datos de la CLABE anterior armaría un registro con una
        # cuenta que ya no es la del proveedor.
        doc.tipo_pago_tef = None
        doc.sucursal_banamex = doc.cuenta_banamex = None
    if not doc.nombre_tef:
        p = frappe.db.get_value("Supplier", doc.party,
                                ["tipo_persona", "nombre_pila", "apellido_paterno", "apellido_materno", "supplier_name"],
                                as_dict=True)
        if p.tipo_persona == "Física" and not (p.nombre_pila or p.apellido_paterno):
            frappe.throw(_("El proveedor {0} es persona física y no tiene nombre ni apellidos capturados. "
                           "Captura nombre y apellidos en el proveedor antes de dar de alta la cuenta: el "
                           "beneficiario del TEF va NOMBRES,PATERNO/MATERNO y no se puede sacar de la razón "
                           "social.").format(doc.party))
        doc.nombre_tef = nombre_tef_para(p.tipo_persona, p.nombre_pila, p.apellido_paterno,
                                         p.apellido_materno, p.supplier_name)
    error = validar_nombre_tef(doc.nombre_tef)
    if error:
        frappe.throw(_("Beneficiario para el TEF: {0}.").format(error))
    antes = doc.get_doc_before_save()
    if antes and doc.verificada and any((antes.get(c) or "") != (doc.get(c) or "") for c in CAMPOS_AL_BANCO):
        # Cambió algo que va al banco: Tesorería tiene que volver a verificar.
        doc.verificada, doc.verificada_por, doc.verificada_el = 0, None, None
    # Candado del servidor: `verificada` es read_only en pantalla, pero eso no frena un save() por
    # API ni por consola. Pasar de 0 a 1 a mano solo lo puede Tesorería. El camino normal,
    # verificar_cuenta(), escribe con db_set y por eso no pasa por aquí (y además marca el flag).
    if doc.verificada and not (antes and antes.verificada) and not doc.flags.get("verificando"):
        if not any(rol in frappe.get_roles() for rol in ROLES_VERIFICAN):
            frappe.throw(_("Solo Tesorería puede marcar una cuenta bancaria como verificada."),
                         frappe.PermissionError)


@frappe.whitelist()
def verificar_cuenta(name):
    """Tesorería confirma que la CLABE y el beneficiario son los que dio el proveedor."""
    roles = frappe.get_roles()
    if not any(rol in roles for rol in ROLES_VERIFICAN):
        frappe.throw(_("Solo Tesorería puede verificar cuentas bancarias."), frappe.PermissionError)
    doc = frappe.get_doc("Bank Account", name)
    if doc.party_type != "Supplier" or not doc.clabe:
        frappe.throw(_("Solo se verifican cuentas de proveedor con CLABE."))
    if frappe.db.get_value("Supplier", doc.party, "bloqueado_pagos"):
        frappe.throw(_("El proveedor {0} está bloqueado para pagos: quita el bloqueo antes de verificar "
                       "su cuenta bancaria.").format(doc.party))
    if doc.verificada:
        # Ya estaba verificada: no reescribir el sello de quién la revisó y cuándo.
        return doc.name
    doc.flags.verificando = True
    doc.db_set({"verificada": 1, "verificada_por": frappe.session.user, "verificada_el": now_datetime()})
    return doc.name
