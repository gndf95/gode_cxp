import frappe


def proveedor_por_rfc(rfc, nombre, moneda="MXN"):
    """Devuelve el Supplier con ese RFC (tax_id); lo crea si no existe."""
    rfc = (rfc or "").upper().strip()
    name = frappe.db.get_value("Supplier", {"tax_id": rfc}, "name")
    if name:
        return name
    fisica = len(rfc) == 13
    grupo = frappe.db.get_single_value("Configuracion CxP", "grupo_proveedores") or "Proveedores CFDI"
    doc = frappe.get_doc({
        "doctype": "Supplier", "supplier_name": (nombre or rfc).strip(), "supplier_group": grupo,
        "supplier_type": "Individual" if fisica else "Company", "tax_id": rfc,
        "tipo_persona": "Física" if fisica else "Moral", "default_currency": moneda if frappe.db.exists("Currency", moneda) else "MXN",
    })
    doc.insert(ignore_permissions=True)
    return doc.name
