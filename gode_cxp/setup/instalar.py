"""Configuración que la app garantiza en cada migración (idempotente)."""
import json

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from gode_cxp.setup.campos import asegurar_campos
from gode_cxp.setup.flujo import asegurar_flujo
from gode_cxp.setup.roles import (
    EDITOR,
    asegurar_perfil_modulos,
    asegurar_permisos,
    asegurar_reportes,
    asegurar_rol_editor,
)

# EDITOR no se le da a nadie a mano: lo reparte asegurar_rol_editor (ver setup/roles.py).
ROLES = ("CxP Revisor", "CxP Tesoreria", "CxP Contabilidad", EDITOR)

# Grupo al que se dan de alta los proveedores nuevos que llegan por CFDI.
GRUPO_PROVEEDORES = "Proveedores CFDI"

# Valores de arranque del pre-registro de cuentas en BancaNet. El `default` del DocType sólo se
# aplica a un documento NUEVO y Configuracion CxP es un Single que ya existe, así que sin esto
# `exigir_preregistro` quedaría en blanco (o sea apagado) después de la migración que lo estrena.
DEFAULTS_PREREGISTRO = {"exigir_preregistro": 1, "importe_maximo_preregistro": 500000,
                        "periodo_preregistro": "DIARIO"}

# --- lista de Bank Account ------------------------------------------------------------------------
# La lista venía con "Compañía" y "Cuenta de la compañía", que en una cuenta de proveedor van SIEMPRE
# vacías (ERPNext sólo las llena cuando `is_company_account`). Lo que Tesorería necesita ver es a
# quién se le paga, por dónde, si la cuenta está verificada y si el banco ya la dio de alta.
# El indicador (`status_field`) lo pinta public/js/bank_account_list.js.
COLUMNAS_CUENTAS_BANCARIAS = ["status_field", "party", "bank", "tipo_pago_tef", "verificada",
                              "estado_preregistro"]
# Las etiquetas van escritas aquí y no sacadas del meta: lo que se guarda en `List View Settings`
# tiene que ser estable entre migraciones o la comparación de idempotencia nunca cuadraría.
ETIQUETAS_CUENTAS_BANCARIAS = {"status_field": "Estado", "party": "Tercero", "bank": "Banco",
                               "tipo_pago_tef": "Naturaleza TEF", "verificada": "Verificada",
                               "estado_preregistro": "Alta en el banco"}
# Campos estándar de ERPNext que dejan de ocupar columna. `account_name` sale porque el ID de la
# lista YA es "<nombre de la cuenta> - <banco>" (Bank Account.autoname), o sea la misma información.
FUERA_DE_LA_LISTA_CUENTAS = ("company", "account", "account_name", "iban", "bank_account_no", "clabe")
# `in_list_view` no alcanza por sí solo: el escritorio recorta a 4, 6 o 10 columnas según el ancho de
# la pantalla (frappe/public/js/frappe/list/list_view.js::setup_columns) y el orden lo da el meta.
# `List View Settings` fija el número y el orden —pero SÓLO reordena lo que ya está en el meta, así
# que las dos mitades son necesarias.
TOTAL_COLUMNAS_CUENTAS = str(len(COLUMNAS_CUENTAS_BANCARIAS) + 2)   # + el ID y las etiquetas


def asegurar_roles():
    for rol in ROLES:
        if not frappe.db.exists("Role", rol):
            frappe.get_doc({"doctype": "Role", "role_name": rol, "desk_access": 1}).insert(ignore_permissions=True)


def raiz_de_grupos_proveedores():
    """La raíz del árbol de Supplier Group (el grupo sin padre).

    OJO con el filtro: ["in", ["", None]] se traduce a IN ('', NULL) y en SQL NULL nunca empata
    dentro de un IN, así que esa consulta devolvía None siempre. Se pregunta con ["is", "not set"],
    que es IFNULL(campo, '') = '' (el mismo defecto ya corregido en facturas/api.py)."""
    return frappe.db.get_value("Supplier Group", {"is_group": 1, "parent_supplier_group": ["is", "not set"]}, "name")


def asegurar_grupo_proveedores():
    if not frappe.db.exists("Supplier Group", GRUPO_PROVEEDORES):
        frappe.get_doc({"doctype": "Supplier Group", "supplier_group_name": GRUPO_PROVEEDORES,
                        "parent_supplier_group": raiz_de_grupos_proveedores(), "is_group": 0}).insert(ignore_permissions=True)


def asegurar_preregistro():
    """Deja el pre-registro de cuentas listo para usarse después de la migración que lo estrena.

    Dos cosas que un `default` no puede hacer: llenar los campos nuevos del Single de configuración
    (que ya existía) y poner 'Sin registrar' en las cuentas de proveedor que se dieron de alta antes
    de que el campo existiera —sin eso quedarían en NULL, no saldrían en la lista de pendientes y el
    candado del lote las tomaría por no registradas sin que nadie pudiera mandarlas al banco."""
    # Se pregunta por la FILA de tabSingles y no por el valor: `get_single_value` convierte según el
    # tipo del campo, así que un Check que nunca se guardó devuelve 0 (no None) y no habría forma de
    # distinguir "apagado a propósito" de "todavía no existe". Con la fila presente no se toca nada:
    # lo que Tesorería configure manda.
    capturados = {fila[0] for fila in frappe.db.sql(
        "select field from tabSingles where doctype = %s", ("Configuracion CxP",))}
    for campo, valor in DEFAULTS_PREREGISTRO.items():
        if campo not in capturados:
            frappe.db.set_single_value("Configuracion CxP", campo, valor)
    frappe.db.sql("""update `tabBank Account` set estado_preregistro = 'Sin registrar'
                     where ifnull(party_type, '') = 'Supplier' and ifnull(estado_preregistro, '') = ''""")


def _fijar_en_la_lista(campo, valor):
    """`in_list_view` de un campo ESTÁNDAR de Bank Account, vía Property Setter.

    Idempotente: `make_property_setter` borra el anterior del mismo (doctype, campo, propiedad) antes
    de insertar, pero se salta la escritura si el valor ya es el que toca para no reescribir la fila
    —y volver a invalidar la caché del DocType— en cada migración."""
    nombre = f"Bank Account-{campo}-in_list_view"
    if frappe.db.get_value("Property Setter", nombre, "value") == str(valor):
        return
    make_property_setter("Bank Account", campo, "in_list_view", valor, "Check",
                         validate_fields_for_doctype=False)


def asegurar_lista_cuentas_bancarias():
    """Deja la lista de cuentas bancarias mostrando lo que sirve para pagarle a un proveedor.

    OJO: `List View Settings` es también lo que guarda el engrane "Configuración de la lista" del
    escritorio, así que esto vuelve a fijar las columnas en cada migración. Si alguien las reordena a
    mano, la siguiente migración las devuelve a estas."""
    for campo in ("party", "bank"):
        _fijar_en_la_lista(campo, 1)
    for campo in FUERA_DE_LA_LISTA_CUENTAS:
        if campo != "clabe":               # `clabe` es campo nuestro: va en setup/campos.py
            _fijar_en_la_lista(campo, 0)
    columnas = json.dumps([{"fieldname": c, "label": ETIQUETAS_CUENTAS_BANCARIAS[c]}
                           for c in COLUMNAS_CUENTAS_BANCARIAS])
    if frappe.db.exists("List View Settings", "Bank Account"):
        ajustes = frappe.get_doc("List View Settings", "Bank Account")
    else:
        ajustes = frappe.new_doc("List View Settings")
        ajustes.name = "Bank Account"
    if (ajustes.fields, ajustes.total_fields) == (columnas, TOTAL_COLUMNAS_CUENTAS):
        return
    ajustes.fields, ajustes.total_fields = columnas, TOTAL_COLUMNAS_CUENTAS
    ajustes.flags.ignore_permissions = True
    ajustes.save()


def asegurar_configuracion():
    asegurar_roles()
    asegurar_grupo_proveedores()
    asegurar_campos()
    # Después de asegurar_campos: los campos del pre-registro tienen que existir para poder llenarlos.
    asegurar_preregistro()
    # Después de asegurar_campos también: las columnas de la lista incluyen campos nuestros.
    asegurar_lista_cuentas_bancarias()
    asegurar_permisos()
    asegurar_reportes()
    asegurar_flujo()
    asegurar_perfil_modulos()
    asegurar_rol_editor()
    frappe.db.commit()
    frappe.clear_cache()
