"""Permisos de los roles CxP sobre DocTypes estándar, reportes, perfil de módulos y rol de edición."""
import frappe
from frappe.permissions import add_permission, update_permission_property

REV, TES, CONTA = "CxP Revisor", "CxP Tesoreria", "CxP Contabilidad"

# CxP Editor NO se le da a nadie a mano y NO recibe permisos de DocType: existe sólo para el
# allow_edit del flujo. "Workflow Document State.allow_edit" es obligatorio (reqd) y admite un solo
# rol por fila; el navegador deja el formulario en solo lectura cuando el usuario no tiene ninguno de
# los roles allow_edit del estado (frappe/public/js/frappe/model/workflow.js::is_read_only). Con un
# rol distinto por estado, Tesorería no podría escribir la nota de rechazo en "En revisión". Por eso
# todos los estados usan este rol y aquí se garantiza que lo tenga quien puede escribir la factura.
# "System Manager" va en la lista porque un administrador humano que no sea el usuario
# "Administrator" no hereda los demás roles (frappe.get_roles("Administrator") sí los devuelve
# todos): sin CxP Editor abriría la factura en solo lectura y no podría corregirla a mano.
EDITOR = "CxP Editor"
ROLES_QUE_ESCRIBEN = (REV, TES, "System Manager")

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

# Reportes estándar que están como atajo en el workspace de CxP.
REPORTES = ("Accounts Payable", "Accounts Payable Summary")

MODULOS_VISIBLES = {"Cuentas por Pagar", "Accounts", "Buying", "Desk", "Core", "Setup", "Contacts", "Stock", "Printing", "Email", "Custom"}


def asegurar_permisos():
    from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

    for rol, doctypes in PERMISOS.items():
        for doctype, concedidos in doctypes.items():
            add_permission(doctype, rol, 0)
            for ptype in PTYPES:
                # validate=False: validar en cada bandera son ~420 pasadas por DocType y hace que
                # 'migrate' tarde minutos (y que un vector a medio escribir reviente la migración).
                update_permission_property(doctype, rol, 0, ptype, 1 if ptype in concedidos else 0, validate=False)
    for doctype in sorted({dt for doctypes in PERMISOS.values() for dt in doctypes}):
        validate_permissions_for_doctype(doctype)
    frappe.clear_cache()


def asegurar_reportes():
    """Report.is_permitted() respeta los roles del reporte estándar (Accounts User, Purchase User,
    Accounts Manager, Auditor), que los roles CxP no tienen. Un Custom Role SUSTITUYE esa lista
    (frappe/core/doctype/report/report.py::is_permitted), así que se guardan los roles originales del
    reporte MÁS los de CxP: nadie que ya lo abría lo pierde."""
    for reporte in REPORTES:
        if not frappe.db.exists("Report", reporte):
            continue
        estandar = frappe.get_all("Has Role", filters={"parenttype": "Report", "parent": reporte}, pluck="role")
        deseados = sorted({r for r in (*estandar, REV, TES, CONTA) if frappe.db.exists("Role", r)})
        name = frappe.db.get_value("Custom Role", {"report": reporte}, "name")
        if name:
            doc = frappe.get_doc("Custom Role", name)
            if sorted({r.role for r in doc.roles}) == deseados:
                continue
        else:
            doc = frappe.new_doc("Custom Role")
            doc.report = reporte
        doc.ref_doctype = frappe.db.get_value("Report", reporte, "ref_doctype")
        doc.set("roles", [{"role": rol} for rol in deseados])
        doc.flags.ignore_permissions = True
        doc.save()


def sellar_rol_editor(doc, method=None):
    """Hook 'validate' de User: quien tenga CxP Revisor o CxP Tesoreria se lleva también CxP Editor,
    porque sin él el flujo le deja el formulario de la factura en solo lectura."""
    if not frappe.db.exists("Role", EDITOR):
        return
    roles_usuario = {fila.role for fila in doc.get("roles") or []}
    if roles_usuario & set(ROLES_QUE_ESCRIBEN) and EDITOR not in roles_usuario:
        doc.append("roles", {"role": EDITOR})


def asegurar_rol_editor():
    """Lo mismo que sellar_rol_editor, para los usuarios que ya existían antes de esta versión."""
    correos = frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", ROLES_QUE_ESCRIBEN]}, pluck="parent")
    for correo in sorted(set(correos)):
        if correo == "Administrator":
            continue    # frappe.get_roles("Administrator") ya devuelve todos los roles
        if frappe.db.exists("Has Role", {"parenttype": "User", "parent": correo, "role": EDITOR}):
            continue
        try:
            frappe.get_doc("User", correo).add_roles(EDITOR)
        except Exception:
            # add_roles guarda el User entero: si ese usuario no valida por cualquier otra razón
            # (un campo obligatorio vacío de antes), no debe tumbar la migración de toda la app.
            # log_error con sólo 'title' (message=None) en v15 mueve el título al mensaje y deja
            # el registro sin título ni traceback: hay que pasar los dos a propósito.
            frappe.log_error(title=f"CxP: no se pudo dar {EDITOR} a {correo}", message=frappe.get_traceback())


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
