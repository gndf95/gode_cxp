"""Eventos de Purchase Invoice: estado inicial, sello de la confirmación de recepción y candado al enviar."""
import frappe
from frappe import _
from frappe.utils import now_datetime


def validar_factura(doc, method=None):
    if doc.cfdi_recibido and not doc.estado_revision:
        doc.estado_revision = "Recibida"
    if doc.recepcion_confirmada and not doc.recepcion_confirmada_por:
        doc.recepcion_confirmada_por = frappe.session.user
        doc.recepcion_confirmada_el = now_datetime()
    if not doc.recepcion_confirmada:
        doc.recepcion_confirmada_por = None
        doc.recepcion_confirmada_el = None


def antes_de_enviar(doc, method=None):
    if doc.cfdi_recibido and doc.estado_revision not in ("Aprobada", "Revisada"):
        frappe.throw(_("La factura {0} no se puede enviar en estado de revisión '{1}'.").format(doc.name, doc.estado_revision))
