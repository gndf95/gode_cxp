"""Puntos de entrada del escritorio para los lotes de pago y para el alta de cuentas en BancaNet.

Sin lógica: sólo comprueba el permiso y delega en pagos.lotes o pagos.preregistro. El permiso se pide
aquí y no en el DocType porque estas llamadas mueven dinero al banco (generar el archivo, declararlo
transmitido, dar de alta la cuenta a la que se le va a pagar) y el permiso de escritura del documento
no alcanza para distinguirlas de guardar una nota.
"""
import frappe
from frappe import _

from gode_cxp.pagos import lotes, preregistro


def _exigir(*roles):
    if not set(roles) & set(frappe.get_roles()):
        frappe.throw(_("No tienes permiso para esta operación (se necesita {0}).").format(" o ".join(roles)),
                     frappe.PermissionError)


def _leer_el_lote(lote):
    """El lote, comprobando el permiso de LECTURA sobre el documento (no el de escritura)."""
    doc = frappe.get_doc("Lote de Pago", lote)
    doc.check_permission("read")
    return doc


def _exigir_el_lote(lote):
    """Además del rol, el permiso de escritura SOBRE EL DOCUMENTO.

    El rol es global; las User Permissions (por empresa, por ejemplo) sólo se aplican mirando el
    documento, y eso lo hace `check_permission`. Sin esto, quien tuviera el rol de Tesorería podía
    generar y transmitir el archivo de un lote de una empresa que no le toca."""
    frappe.get_doc("Lote de Pago", lote).check_permission("write")


def _exigir_las_cuentas(nombres):
    """Lo mismo que `_exigir_el_lote`, pero sobre CADA cuenta bancaria que la operación va a tocar.

    Dar de alta una cuenta en el banco es lo que habilita a ese proveedor para cobrar, así que el rol
    (que es global) no alcanza: las User Permissions por empresa o por proveedor sólo se aplican
    mirando el documento."""
    for name in nombres:
        frappe.get_doc("Bank Account", name).check_permission("write")


@frappe.whitelist()
def facturas_pagables(company, proveedor=None, hasta_vencimiento=None):
    """Consultar qué se puede pagar también le sirve a quien revisa y a contabilidad."""
    _exigir("CxP Tesoreria", "CxP Revisor", "CxP Contabilidad", "System Manager")
    # `lotes.facturas_pagables` consulta con `frappe.get_all`, que va con `ignore_permissions=True`:
    # el permiso de las facturas y de la empresa hay que pedirlo aquí o la consulta se salta las
    # User Permissions y devuelve las facturas de una empresa que el usuario no puede ver.
    frappe.has_permission("Purchase Invoice", "read", throw=True)
    frappe.has_permission("Company", "read", doc=company, throw=True)
    return lotes.facturas_pagables(company, proveedor, hasta_vencimiento)


@frappe.whitelist()
def crear_lotes(company, fecha_pago, partidas):
    """partidas llega del diálogo del escritorio como texto JSON; lotes.crear_lotes lo desarma."""
    _exigir("CxP Tesoreria", "System Manager")
    return lotes.crear_lotes(company, fecha_pago, partidas)


@frappe.whitelist()
def generar_archivo(lote):
    _exigir("CxP Tesoreria", "System Manager")
    _exigir_el_lote(lote)
    return lotes.generar_archivo(lote)


@frappe.whitelist()
def marcar_transmitido(lote, autorizacion):
    _exigir("CxP Tesoreria", "System Manager")
    _exigir_el_lote(lote)
    lotes.marcar_transmitido(lote, autorizacion)
    return lote


@frappe.whitelist()
def descargar_archivo(lote):
    """Baja el archivo TEF del lote, forzando la descarga.

    El `file_url` del adjunto privado se lo sirve Frappe al navegador y el .txt acaba abierto como
    texto en otra pestaña; BancaNet necesita el archivo en disco. `frappe.response.type = "download"`
    es lo que hace que la respuesta salga con `Content-Disposition: attachment`
    (frappe/utils/response.py::as_raw), con el nombre que espera el banco.

    Leer el archivo no mueve dinero, así que basta con poder LEER el lote: lo piden los mismos roles
    que pueden abrirlo (Tesorería, Revisor y Contabilidad), más el permiso por documento, porque el
    archivo lleva las cuentas bancarias de los proveedores."""
    _exigir("CxP Tesoreria", "CxP Revisor", "CxP Contabilidad", "System Manager")
    doc = _leer_el_lote(lote)
    if not doc.archivo_tef:
        frappe.throw(_("El lote {0} todavía no tiene archivo del banco: genéralo primero.").format(doc.name))
    adjunto = frappe.db.get_value("File", {"file_url": doc.archivo_tef, "attached_to_doctype": "Lote de Pago",
                                           "attached_to_name": doc.name}, "name")
    if not adjunto:
        frappe.throw(_("El archivo {0} del lote {1} ya no está adjunto: vuelve a generarlo.")
                     .format(doc.archivo_tef, doc.name))
    archivo = frappe.get_doc("File", adjunto)
    frappe.response["filename"] = doc.nombre_archivo or archivo.file_name
    # En BYTES, tal cual se escribió: el archivo va en ancho fijo y con CRLF, y `File.get_content()`
    # devolvería `str` (intenta `.decode()` con todo lo que no reviente, como este .txt).
    with open(archivo.get_full_path(), "rb") as f:
        frappe.response["filecontent"] = f.read()
    frappe.response["type"] = "download"


@frappe.whitelist()
def nuevo_lote_pendientes(lote):
    _exigir("CxP Tesoreria", "System Manager")
    _exigir_el_lote(lote)
    return lotes.nuevo_lote_pendientes(lote)


# --- alta de las cuentas de proveedor en BancaNet (pagos/preregistro.py) --------------------------
# Dar de alta una cuenta en el banco es lo que abre la puerta a pagarle a ese proveedor: es de
# Tesorería, igual que verificar la cuenta.

@frappe.whitelist()
def descargar_preregistro(cuentas=None):
    _exigir("CxP Tesoreria", "System Manager")
    # Las cuentas se resuelven antes de generar el archivo para poder pedir el permiso de cada una.
    nombres = preregistro.nombres_por_registrar(cuentas)
    _exigir_las_cuentas(nombres)
    return preregistro.descargar_preregistro(nombres)


@frappe.whitelist()
def aplicar_respuesta_preregistro(file_url):
    _exigir("CxP Tesoreria", "System Manager")
    # De qué cuentas habla el archivo sólo se sabe después de cruzarlo, y el cruce no escribe nada:
    # el permiso se pide en medio, antes de mover un solo estado.
    cruce = preregistro.cruzar_respuesta(file_url)
    _exigir_las_cuentas(sorted({par["cuenta"] for par in cruce if par["cuenta"]}))
    return preregistro.aplicar_cruce(cruce)


@frappe.whitelist()
def marcar_registrada(cuentas):
    _exigir("CxP Tesoreria", "System Manager")
    nombres = preregistro.lista_de_cuentas(cuentas)
    _exigir_las_cuentas(nombres or [])
    return preregistro.marcar_registrada(nombres)
