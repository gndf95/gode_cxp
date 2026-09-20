"""Eventos de Purchase Invoice: estado inicial, sello de la confirmación de recepción y candado al enviar."""
import frappe
from frappe import _
from frappe.utils import now_datetime


def validar_factura(doc, method=None):
    if doc.is_new() and doc.get("amended_from"):
        _reiniciar_revision_de_la_enmienda(doc)
    if doc.cfdi_recibido and not doc.estado_revision:
        doc.estado_revision = "Recibida"
    antes = doc.get_doc_before_save() if not doc.is_new() else None
    if antes and antes.estado_revision == "Revisada" and doc.estado_revision == "Recibida":
        # "Regresar a recibida" deshace la confirmación: quien la vuelva a confirmar deja su propio sello.
        doc.recepcion_confirmada = 0
    if doc.estado_revision == "Revisada" and not doc.recepcion_confirmada:
        # La acción "Confirmar recepción" ES la confirmación: ya no hay que marcar la casilla y
        # guardar aparte. `apply_workflow` pone el estado nuevo en memoria y luego guarda
        # (frappe/model/workflow.py), así que aquí ya se ve "Revisada" y el sello se pone solo.
        doc.recepcion_confirmada = 1
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


def apuntar_cfdi_a_la_enmienda(doc, method=None):
    """La enmienda conserva el enlace al CFDI Recibido, así que el CFDI tiene que apuntar a ella y
    no a la factura cancelada; si no, 'Ver factura' y crear_factura_desde_cfdi seguirían mandando al
    documento muerto. Va en 'after_insert' y no en 'validate' porque hasta ahí no hay fila en la base
    a la que el enlace pueda apuntar."""
    if doc.get("amended_from") and doc.cfdi_recibido:
        frappe.db.set_value("CFDI Recibido", doc.cfdi_recibido, "factura", doc.name)


def antes_de_enviar(doc, method=None):
    if doc.cfdi_recibido and doc.estado_revision not in ("Aprobada", "Revisada"):
        frappe.throw(_("La factura {0} no se puede enviar en estado de revisión '{1}'.").format(doc.name, doc.estado_revision))
