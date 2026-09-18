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


def asegurar_configuracion():
    asegurar_roles()
    asegurar_grupo_proveedores()
    asegurar_campos()
    asegurar_permisos()
    asegurar_reportes()
    asegurar_flujo()
    asegurar_perfil_modulos()
    asegurar_rol_editor()
    frappe.db.commit()
    frappe.clear_cache()
