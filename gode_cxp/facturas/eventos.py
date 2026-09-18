"""Eventos de Purchase Invoice: estado inicial, sello de la confirmación de recepción y candado al enviar."""
import frappe
from frappe import _
from frappe.utils import now_datetime


def validar_factura(doc, method=None):
    if doc.is_new() and doc.get("amended_from"):
        _reiniciar_revision_de_la_enmienda(doc)
    if doc.cfdi_recibido and not doc.estado_revision:
        doc.estado_revision = "Recibida"
    if doc.recepcion_confirmada and not doc.recepcion_confirmada_por:
        doc.recepcion_confirmada_por = frappe.session.user
        doc.recepcion_confirmada_el = now_datetime()
    if not doc.recepcion_confirmada:
        doc.recepcion_confirmada_por = None
        doc.recepcion_confirmada_el = None


def _reiniciar_revision_de_la_enmienda(doc):
    """El botón 'Amend' del escritorio copia hasta los campos no_copy
    (frappe/public/js/frappe/model/create_new.js: `!from_amend && df.no_copy`), así que la enmienda
    llegaría con el UUID del CFDI —índice único, duplicado— y con el estado 'Aprobada', que
    frappe.model.workflow.validate_workflow rechaza por no venir de ninguna transición.
    La enmienda se revisa desde cero: el UUID se queda en la factura cancelada, que es el documento
    que quedó timbrado; el enlace al CFDI Recibido sí se conserva para que siga bajo el flujo."""
    doc.cfdi_uuid = None
    doc.estado_revision = None
    doc.recepcion_confirmada = 0
    doc.nota_aclaracion = None


def antes_de_enviar(doc, method=None):
    if doc.cfdi_recibido and doc.estado_revision not in ("Aprobada", "Revisada"):
        frappe.throw(_("La factura {0} no se puede enviar en estado de revisión '{1}'.").format(doc.name, doc.estado_revision))
