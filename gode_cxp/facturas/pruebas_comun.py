"""Prepara el sitio de pruebas (cuentas, artículo, configuración) y limpia lo que crean las pruebas."""
import frappe

from gode_cxp.cfdi import ejemplos

EMPRESA = "GODE PRUEBAS"
RFCS_PRUEBA = ("AVI900101AB1", "AVI900101AB2", "HESB850101AB1")
USUARIOS_PRUEBA = ("prueba.revisor@cxp.local", "prueba.tesoreria@cxp.local", "prueba.conta@cxp.local",
                   "prueba.sysadmin@cxp.local", "prueba.contable@cxp.local")


def _cuenta(nombre, root_type, account_type=None):
    """Crea la cuenta bajo el primer grupo del root_type de la empresa; devuelve su name."""
    empresa = frappe.get_doc("Company", EMPRESA)
    completo = f"{nombre} - {empresa.abbr}"
    if frappe.db.exists("Account", completo):
        return completo
    padre = frappe.db.get_value("Account", {"company": EMPRESA, "root_type": root_type, "is_group": 1, "parent_account": ["!=", ""]}, "name")
    doc = frappe.get_doc({"doctype": "Account", "account_name": nombre, "parent_account": padre, "company": EMPRESA,
                          "root_type": root_type, "is_group": 0, "account_type": account_type})
    doc.insert(ignore_permissions=True)
    return doc.name


def preparar_sitio_pruebas():
    if not frappe.db.exists("Company", EMPRESA):
        frappe.throw(f"No existe la empresa '{EMPRESA}': preparar_sitio_pruebas() solo corre en el sitio de pruebas.")
    empresa = frappe.get_doc("Company", EMPRESA)
    empresa.default_payable_account = frappe.db.get_value("Account", {"company": EMPRESA, "account_type": "Payable", "is_group": 0}, "name")
    empresa.save(ignore_permissions=True)
    cuentas = {
        "cuenta_gasto_default": _cuenta("Gastos CFDI prueba", "Expense"),
        "cuenta_iva_acreditable": _cuenta("IVA acreditable prueba", "Asset", "Tax"),
        "cuenta_ieps": _cuenta("IEPS acreditable prueba", "Asset", "Tax"),
        "cuenta_ret_iva": _cuenta("IVA retenido prueba", "Liability", "Tax"),
        "cuenta_ret_isr": _cuenta("ISR retenido prueba", "Liability", "Tax"),
    }
    if not frappe.db.exists("Item", "CFDI-CONCEPTO"):
        frappe.get_doc({"doctype": "Item", "item_code": "CFDI-CONCEPTO", "item_name": "Concepto CFDI", "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
                        "stock_uom": frappe.db.get_value("UOM", {"name": ["in", ["Nos", "Nos.", "Unit", "Unidad(es)"]]}, "name"),
                        "is_stock_item": 0, "is_purchase_item": 1, "is_sales_item": 0}).insert(ignore_permissions=True)
    conf = frappe.get_doc("Configuracion CxP")
    conf.update({"rfc_empresa": ejemplos.RFC_EMPRESA, "empresa": EMPRESA, "item_generico": "CFDI-CONCEPTO",
                 "grupo_proveedores": "Proveedores CFDI", "dias_credito_default": 30, "modo_pruebas_correo": 1, **cuentas})
    conf.save(ignore_permissions=True)
    return cuentas


def limpiar():
    # Red de seguridad: limpiar() borra CFDI y facturas en bloque, nunca debe tocar producción.
    if frappe.db.get_single_value("Configuracion CxP", "empresa") != EMPRESA:
        frappe.throw(f"limpiar() solo corre en el sitio de pruebas (Configuración CxP.empresa = {EMPRESA})")
    # Dos filtros para Purchase Invoice: la enmienda de una factura pierde el UUID (ver
    # facturas/eventos.py) pero conserva el enlace al CFDI, y también hay que borrarla.
    for dt, filtros in (("Purchase Invoice", {"cfdi_uuid": ["!=", ""]}),
                        ("Purchase Invoice", {"cfdi_recibido": ["!=", ""]}),
                        ("CFDI Recibido", {})):
        for name in frappe.get_all(dt, filters=filtros, pluck="name"):
            doc = frappe.get_doc(dt, name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc(dt, name, force=1, ignore_permissions=True, delete_permanently=True)
    for name in frappe.get_all("Supplier", filters={"tax_id": ["in", RFCS_PRUEBA]}, pluck="name"):
        frappe.delete_doc("Supplier", name, force=1, ignore_permissions=True)
    # Los usuarios de prueba no deben quedar vivos en el sitio; test_flujo los vuelve a crear.
    for correo in USUARIOS_PRUEBA:
        if frappe.db.exists("User", correo):
            frappe.delete_doc("User", correo, force=1, ignore_permissions=True, delete_permanently=True)
    frappe.db.commit()
