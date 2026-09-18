"""Prepara el sitio de pruebas (cuentas, artículo, configuración) y limpia lo que crean las pruebas."""
import frappe

from gode_cxp.cfdi import ejemplos
from gode_cxp.setup.produccion import CUENTAS, configurar_empresa

EMPRESA = "GODE PRUEBAS"
RFCS_PRUEBA = ("AVI900101AB1", "AVI900101AB2", "HESB850101AB1")
USUARIOS_PRUEBA = ("prueba.revisor@cxp.local", "prueba.tesoreria@cxp.local", "prueba.conta@cxp.local",
                   "prueba.sysadmin@cxp.local", "prueba.contable@cxp.local")


def preparar_sitio_pruebas():
    """Deja el sitio de pruebas listo con el MISMO script que se correrá en producción
    (setup/produccion.configurar_empresa), para no tener dos configuraciones que puedan divergir.
    Lo único que se agrega aparte es el modo de pruebas del correo."""
    if not frappe.db.exists("Company", EMPRESA):
        frappe.throw(f"No existe la empresa '{EMPRESA}': preparar_sitio_pruebas() solo corre en el sitio de pruebas.")
    configurar_empresa(EMPRESA, ejemplos.RFC_EMPRESA, dry_run=False)
    if not frappe.db.get_single_value("Configuracion CxP", "modo_pruebas_correo"):
        frappe.db.set_single_value("Configuracion CxP", "modo_pruebas_correo", 1)
    conf = frappe.get_doc("Configuracion CxP")
    return {campo: conf.get(campo) for campo in CUENTAS}


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
    # Las pruebas de la bandeja suben XML, ZIP y PDF sueltos (File sin adjuntar). Los que la carga
    # no borra (un archivo ajeno, uno que falló) se quedarían acumulándose en el sitio de pruebas.
    for name in frappe.get_all("File", filters={"attached_to_doctype": ["is", "not set"], "is_folder": 0},
                               or_filters=[["file_name", "like", "%.xml"], ["file_name", "like", "%.zip"], ["file_name", "like", "%.pdf"]],
                               pluck="name"):
        frappe.delete_doc("File", name, force=1, ignore_permissions=True, delete_permanently=True)
    frappe.db.commit()
