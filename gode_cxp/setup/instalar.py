"""Configuración que la app garantiza en cada migración (idempotente)."""
import frappe

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


def asegurar_configuracion():
    asegurar_roles()
    asegurar_grupo_proveedores()
    asegurar_campos()
    # Después de asegurar_campos: los campos del pre-registro tienen que existir para poder llenarlos.
    asegurar_preregistro()
    asegurar_permisos()
    asegurar_reportes()
    asegurar_flujo()
    asegurar_perfil_modulos()
    asegurar_rol_editor()
    frappe.db.commit()
    frappe.clear_cache()
