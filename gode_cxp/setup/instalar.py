"""Configuración que la app garantiza en cada migración (idempotente)."""
import frappe

from gode_cxp.setup.campos import asegurar_campos
from gode_cxp.setup.flujo import asegurar_flujo
from gode_cxp.setup.roles import asegurar_perfil_modulos, asegurar_permisos

ROLES = ("CxP Revisor", "CxP Tesoreria", "CxP Contabilidad")


def asegurar_roles():
    for rol in ROLES:
        if not frappe.db.exists("Role", rol):
            frappe.get_doc({"doctype": "Role", "role_name": rol, "desk_access": 1}).insert(ignore_permissions=True)


def asegurar_grupo_proveedores():
    if not frappe.db.exists("Supplier Group", "Proveedores CFDI"):
        raiz = frappe.db.get_value("Supplier Group", {"is_group": 1, "parent_supplier_group": ["in", ["", None]]}, "name")
        frappe.get_doc({"doctype": "Supplier Group", "supplier_group_name": "Proveedores CFDI", "parent_supplier_group": raiz, "is_group": 0}).insert(ignore_permissions=True)


def asegurar_configuracion():
    asegurar_roles()
    asegurar_grupo_proveedores()
    asegurar_campos()
    asegurar_permisos()
    asegurar_flujo()
    asegurar_perfil_modulos()
    frappe.db.commit()
    frappe.clear_cache()
