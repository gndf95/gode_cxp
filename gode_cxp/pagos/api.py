"""Puntos de entrada del escritorio para los lotes de pago.

Sin lógica: sólo comprueba el rol y delega en pagos.lotes. El permiso se pide aquí y no en el DocType
porque estas llamadas mueven dinero al banco (generar el archivo, declararlo transmitido) y el
permiso de escritura del Lote de Pago no alcanza para distinguirlas de guardar una nota.
"""
import frappe
from frappe import _

from gode_cxp.pagos import lotes


def _exigir(*roles):
    if not set(roles) & set(frappe.get_roles()):
        frappe.throw(_("No tienes permiso para esta operación (se necesita {0}).").format(" o ".join(roles)),
                     frappe.PermissionError)


@frappe.whitelist()
def facturas_pagables(company, proveedor=None, hasta_vencimiento=None):
    """Consultar qué se puede pagar también le sirve a quien revisa y a contabilidad."""
    _exigir("CxP Tesoreria", "CxP Revisor", "CxP Contabilidad", "System Manager")
    return lotes.facturas_pagables(company, proveedor, hasta_vencimiento)


@frappe.whitelist()
def crear_lotes(company, fecha_pago, partidas):
    """partidas llega del diálogo del escritorio como texto JSON; lotes.crear_lotes lo desarma."""
    _exigir("CxP Tesoreria", "System Manager")
    return lotes.crear_lotes(company, fecha_pago, partidas)


@frappe.whitelist()
def generar_archivo(lote):
    _exigir("CxP Tesoreria", "System Manager")
    return lotes.generar_archivo(lote)


@frappe.whitelist()
def marcar_transmitido(lote, autorizacion):
    _exigir("CxP Tesoreria", "System Manager")
    lotes.marcar_transmitido(lote, autorizacion)
    return lote


@frappe.whitelist()
def nuevo_lote_pendientes(lote):
    _exigir("CxP Tesoreria", "System Manager")
    return lotes.nuevo_lote_pendientes(lote)
