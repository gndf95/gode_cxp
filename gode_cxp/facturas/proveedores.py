import frappe
from frappe import _


def proveedor_por_rfc(rfc, nombre, moneda="MXN"):
    """Devuelve el Supplier con ese RFC (tax_id); lo crea si no existe."""
    rfc = (rfc or "").upper().strip()
    if not rfc:
        frappe.throw(_("No se puede buscar ni crear un proveedor sin RFC."))
    name = frappe.db.get_value("Supplier", {"tax_id": rfc}, "name")
    if name:
        return name
    fisica = len(rfc) == 13
    grupo = frappe.db.get_single_value("Configuracion CxP", "grupo_proveedores") or "Proveedores CFDI"
    nombre = (nombre or rfc).strip()
    datos = {
        "doctype": "Supplier", "supplier_name": nombre[:140], "supplier_group": grupo,
        "supplier_type": "Individual" if fisica else "Company", "tax_id": rfc,
        "tipo_persona": "Física" if fisica else "Moral",
        "default_currency": moneda if frappe.db.exists("Currency", moneda) else "MXN",
    }
    try:
        doc = frappe.get_doc(dict(datos))
        doc.insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        # Otro RFC ya usa ese nombre (el name del Supplier es el nombre): se desambigua con el RFC.
        datos["supplier_name"] = f"{nombre[:120]} ({rfc})"
        doc = frappe.get_doc(dict(datos))
        doc.insert(ignore_permissions=True)
    return doc.name
