"""Workflow de revisión de facturas de compra (idempotente: se reescribe en cada migración)."""
import frappe

from gode_cxp.setup.roles import EDITOR, REV, TES

NOMBRE = "Revision de facturas CxP"

# PENDIENTE (Task 9): al activar el flujo, Workflow.update_default_workflow_status rellena
# estado_revision en TODAS las facturas de compra que lo tengan vacío (los borradores quedan en
# "Recibida" y las enviadas en "Aprobada"). En producción eso etiqueta facturas viejas sin CFDI como
# "Aprobadas": hay que decidir qué hacer con ese estampado masivo antes de migrar rh.urenque.com.

# (estado, doc_status). allow_edit es obligatorio y admite un solo rol por fila: todos los estados
# usan EDITOR, que la app le da a revisores y a tesorería (ver setup/roles.py).
ESTADOS = [
    ("Recibida", "0"), ("En revisión", "0"), ("En aclaración", "0"), ("Revisada", "0"),
    ("Aprobada", "1"), ("Rechazada", "0"), ("Error de lectura", "0"),
]
TRANSICIONES = [  # (estado, acción, siguiente, rol, condición)
    ("Recibida", "Enviar a revisión", "En revisión", REV, ""),
    ("Recibida", "Enviar a revisión", "En revisión", TES, ""),
    ("En revisión", "Confirmar recepción", "Revisada", REV, "doc.recepcion_confirmada == 1"),
    ("En revisión", "Confirmar recepción", "Revisada", TES, "doc.recepcion_confirmada == 1"),
    ("En revisión", "Pedir aclaración", "En aclaración", REV, "doc.nota_aclaracion"),
    ("En revisión", "Pedir aclaración", "En aclaración", TES, "doc.nota_aclaracion"),
    ("En aclaración", "Reanudar", "En revisión", REV, ""),
    ("En aclaración", "Reanudar", "En revisión", TES, ""),
    ("Revisada", "Aprobar", "Aprobada", TES, ""),
    ("Revisada", "Rechazar", "Rechazada", TES, "doc.nota_aclaracion"),
    ("En revisión", "Rechazar", "Rechazada", TES, "doc.nota_aclaracion"),
    ("Error de lectura", "Corregida", "Recibida", TES, "doc.grand_total"),
]


def asegurar_flujo():
    for estado, _ in ESTADOS:
        if not frappe.db.exists("Workflow State", estado):
            frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": estado, "style": ""}).insert(ignore_permissions=True)
    for _, accion, _, _, _ in TRANSICIONES:
        if not frappe.db.exists("Workflow Action Master", accion):
            frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": accion}).insert(ignore_permissions=True)
    if frappe.db.exists("Workflow", NOMBRE):
        wf = frappe.get_doc("Workflow", NOMBRE)
    else:
        wf = frappe.new_doc("Workflow")
        wf.workflow_name = NOMBRE
    wf.document_type = "Purchase Invoice"
    wf.workflow_state_field = "estado_revision"
    wf.is_active = 1
    wf.send_email_alert = 0
    wf.override_status = 0
    wf.set("states", [])
    for estado, doc_status in ESTADOS:
        wf.append("states", {"state": estado, "doc_status": doc_status, "allow_edit": EDITOR, "update_field": "", "update_value": ""})
    wf.set("transitions", [])
    for estado, accion, siguiente, rol, condicion in TRANSICIONES:
        wf.append("transitions", {"state": estado, "action": accion, "next_state": siguiente, "allowed": rol,
                                   "allow_self_approval": 1, "condition": condicion})
    wf.flags.ignore_permissions = True
    wf.save()
