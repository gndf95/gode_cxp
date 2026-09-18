"""Permisos de los roles CxP sobre DocTypes estándar, perfil de módulos y asignación de vector completo."""
import frappe
from frappe.permissions import add_permission, update_permission_property

PTYPES = ("read", "write", "create", "delete", "submit", "cancel", "amend", "print", "email", "report", "export", "share", "import", "select")

PERMISOS = {
    "CxP Revisor": {
        "Purchase Invoice": ("read", "write", "create", "print", "email", "report", "export", "select"),
        "Supplier": ("read", "select"), "Item": ("read", "select"), "Account": ("read", "select"), "Company": ("read", "select"),
        "Purchase Order": ("read", "select"), "Purchase Receipt": ("read", "select"), "File": ("read", "write", "create"),
    },
    "CxP Tesoreria": {
        "Purchase Invoice": ("read", "write", "create", "submit", "cancel", "amend", "print", "email", "report", "export", "select"),
        "Supplier": ("read", "write", "create", "print", "report", "export", "select"), "Item": ("read", "select"), "Account": ("read", "select"),
        "Company": ("read", "select"), "Purchase Order": ("read", "select"), "Purchase Receipt": ("read", "select"), "File": ("read", "write", "create"),
        "Payment Entry": ("read", "report", "select"), "Bank Account": ("read", "select"),
    },
    "CxP Contabilidad": {
        "Purchase Invoice": ("read", "print", "report", "export", "select"), "Supplier": ("read", "report", "select"),
        "Payment Entry": ("read", "report", "export", "select"), "Account": ("read", "select"), "Company": ("read", "select"),
    },
}

MODULOS_VISIBLES = {"Cuentas por Pagar", "Accounts", "Buying", "Desk", "Core", "Setup", "Contacts", "Stock", "Printing", "Email", "Custom"}


def asegurar_permisos():
    for rol, doctypes in PERMISOS.items():
        for doctype, concedidos in doctypes.items():
            add_permission(doctype, rol, 0)
            for ptype in PTYPES:
                update_permission_property(doctype, rol, 0, ptype, 1 if ptype in concedidos else 0)
    frappe.clear_cache()


def asegurar_perfil_modulos():
    bloquear = sorted(m for m in frappe.get_all("Module Def", pluck="name") if m not in MODULOS_VISIBLES)
    if frappe.db.exists("Module Profile", "Cuentas por Pagar"):
        perfil = frappe.get_doc("Module Profile", "Cuentas por Pagar")
    else:
        perfil = frappe.new_doc("Module Profile")
        perfil.module_profile_name = "Cuentas por Pagar"
    perfil.set("block_modules", [{"module": m} for m in bloquear])
    perfil.flags.ignore_permissions = True
    perfil.save()
    # on_update encola update_all_users y deja el documento bloqueado: se guarda una sola vez y se libera.
    perfil.unlock()
