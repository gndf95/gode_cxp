"""Entrada de XML: crea el CFDI Recibido (o devuelve el existente si el UUID ya está)."""
import frappe
from frappe import _

from gode_cxp.cfdi.errores import CfdiInvalido
from gode_cxp.cfdi.lector import leer_cfdi


def rfc_empresa():
    return (frappe.db.get_single_value("Configuracion CxP", "rfc_empresa") or "").strip().upper()


def procesar_xml(xml_bytes, origen, nombre_archivo="cfdi.xml", pdf_bytes=None):
    if not rfc_empresa():
        frappe.throw(_("Configura el RFC de la empresa en Configuración CxP antes de cargar CFDI."))
    try:
        datos = leer_cfdi(xml_bytes)
    except CfdiInvalido as e:
        frappe.throw(_("XML rechazado: {0}").format(e), title=_("CFDI inválido"))

    existente = frappe.db.get_value("CFDI Recibido", {"uuid": datos["uuid"]}, "name")
    if existente:
        doc = frappe.get_doc("CFDI Recibido", existente)
        doc.flags.duplicado = True
        return doc

    doc = frappe.new_doc("CFDI Recibido")
    doc.flags.duplicado = False
    doc.origen = origen
    for campo in ("uuid", "tipo_comprobante", "serie", "folio", "fecha_emision", "fecha_timbrado", "rfc_emisor", "nombre_emisor",
                  "rfc_receptor", "nombre_receptor", "uso_cfdi", "moneda", "tipo_cambio", "subtotal", "descuento",
                  "iva_trasladado", "ieps", "iva_retenido", "isr_retenido", "total", "metodo_pago", "forma_pago"):
        doc.set(campo, datos[campo])
    doc.version_cfdi = datos["version"]
    for c in datos["conceptos"]:
        doc.append("conceptos", c)
    if not frappe.db.exists("Currency", doc.moneda):
        doc.moneda = "MXN"   # XXX (complementos de pago) no existe como moneda
    doc.estado = "Nuevo" if doc.rfc_receptor == rfc_empresa() else "Ajeno"
    if doc.rfc_emisor:
        doc.proveedor = frappe.db.get_value("Supplier", {"tax_id": doc.rfc_emisor}, "name")
    doc.insert(ignore_permissions=True)

    doc.archivo_xml = _adjuntar(doc, nombre_archivo, xml_bytes)
    if pdf_bytes:
        doc.archivo_pdf = _adjuntar(doc, nombre_archivo.rsplit(".", 1)[0] + ".pdf", pdf_bytes)
    doc.db_update()
    return doc


def _adjuntar(doc, nombre, contenido):
    archivo = frappe.get_doc({
        "doctype": "File", "file_name": nombre, "content": contenido, "is_private": 1,
        "attached_to_doctype": doc.doctype, "attached_to_name": doc.name,
    })
    archivo.insert(ignore_permissions=True)
    return archivo.file_url
