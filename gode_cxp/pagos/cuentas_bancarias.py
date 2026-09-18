"""Cuentas bancarias de proveedor para el archivo TEF: CLABE, naturaleza, nombre del beneficiario y verificación."""
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import now_datetime

BANCO_BANAMEX = "002"          # código de banco dentro de la CLABE
PESOS_CLABE = (3, 7, 1)        # ponderación cíclica del dígito verificador (Banxico)
PERMITIDOS_TEF = re.compile(r"^[A-Z0-9 ,/]*$")
LARGO_NOMBRE_TEF = 55
# Campos que viajan al banco: si cambia uno, la verificación de Tesorería deja de valer.
CAMPOS_AL_BANCO = ("clabe", "sucursal_banamex", "cuenta_banamex", "nombre_tef")
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
    s = "".join(c for c in s if not unicodedata.combining(c)).upper().replace("Ñ", "N").replace(".", "")
    s = "".join(c if PERMITIDOS_TEF.match(c) else " " for c in s)
    return re.sub(r"\s+", " ", s).strip()


def nombre_tef_para(tipo_persona, nombre_pila, apellido_paterno, apellido_materno, razon_social):
    """Física: NOMBRES,PATERNO/MATERNO · Moral: PRIMERA,RESTO/ (formato aceptado por Banamex el 17/09/2026)."""
    if tipo_persona == "Física" and (nombre_pila or apellido_paterno):
        s = f"{transliterar(nombre_pila)},{transliterar(apellido_paterno)}/{transliterar(apellido_materno)}"
    else:
        palabras = transliterar(razon_social or nombre_pila).replace(",", " ").replace("/", " ").split()
        s = f"{palabras[0]},{' '.join(palabras[1:])}/" if palabras else ""
    return s[:LARGO_NOMBRE_TEF]


def validar_nombre_tef(nombre):
    """None si es válido; si no, el motivo en español."""
    nombre = nombre or ""
    if not nombre.strip():
        return "el beneficiario está vacío"
    if len(nombre) > LARGO_NOMBRE_TEF:
        return f"el beneficiario pasa de {LARGO_NOMBRE_TEF} caracteres"
    if "." in nombre:
        return "el beneficiario no puede llevar punto"
    if not PERMITIDOS_TEF.match(nombre):
        return "el beneficiario solo admite mayúsculas sin acentos, dígitos, espacio, coma y diagonal"
    if nombre.count(",") != 1:
        return "el beneficiario debe llevar exactamente una coma (NOMBRES,PATERNO/MATERNO o PRIMERA,RESTO/)"
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
        doc.tipo_pago_tef = None
    if not doc.nombre_tef:
        p = frappe.db.get_value("Supplier", doc.party,
                                ["tipo_persona", "nombre_pila", "apellido_paterno", "apellido_materno", "supplier_name"],
                                as_dict=True)
        doc.nombre_tef = nombre_tef_para(p.tipo_persona, p.nombre_pila, p.apellido_paterno,
                                         p.apellido_materno, p.supplier_name)
    error = validar_nombre_tef(doc.nombre_tef)
    if error:
        frappe.throw(_("Beneficiario para el TEF: {0}.").format(error))
    antes = doc.get_doc_before_save()
    if antes and doc.verificada and any((antes.get(c) or "") != (doc.get(c) or "") for c in CAMPOS_AL_BANCO):
        # Cambió algo que va al banco: Tesorería tiene que volver a verificar.
        doc.verificada, doc.verificada_por, doc.verificada_el = 0, None, None


@frappe.whitelist()
def verificar_cuenta(name):
    """Tesorería confirma que la CLABE y el beneficiario son los que dio el proveedor."""
    roles = frappe.get_roles()
    if not any(rol in roles for rol in ROLES_VERIFICAN):
        frappe.throw(_("Solo Tesorería puede verificar cuentas bancarias."), frappe.PermissionError)
    doc = frappe.get_doc("Bank Account", name)
    if doc.party_type != "Supplier" or not doc.clabe:
        frappe.throw(_("Solo se verifican cuentas de proveedor con CLABE."))
    doc.db_set({"verificada": 1, "verificada_por": frappe.session.user, "verificada_el": now_datetime()})
    return doc.name
