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
# El camino normal son DOS clics: "Confirmar recepción" (Revisor o Tesorería) y "Aprobar"
# (Tesorería). El paso "Enviar a revisión" se retiró —era un trámite de por medio y con ~1000
# facturas al mes costaba caro— y la casilla `recepcion_confirmada` ya no es condición de nada: la
# propia acción la marca (facturas/eventos.validar_factura). Lo que NO se junta es confirmar y
# aprobar: siguen siendo dos transiciones para que queden dos registros, quién recibió y quién pagó.
#
# El estado "En revisión" se conserva (puede haber facturas ahí cuando migre producción) y desde él
# se puede confirmar la recepción, pedir aclaración o rechazar; simplemente ya no se llega a él.
TRANSICIONES = [  # (estado, acción, siguiente, rol, condición)
    ("Recibida", "Confirmar recepción", "Revisada", REV, ""),
    ("Recibida", "Confirmar recepción", "Revisada", TES, ""),
    ("En revisión", "Confirmar recepción", "Revisada", REV, ""),
    ("En revisión", "Confirmar recepción", "Revisada", TES, ""),
    ("Recibida", "Pedir aclaración", "En aclaración", REV, "doc.nota_aclaracion"),
    ("Recibida", "Pedir aclaración", "En aclaración", TES, "doc.nota_aclaracion"),
    ("En revisión", "Pedir aclaración", "En aclaración", REV, "doc.nota_aclaracion"),
    ("En revisión", "Pedir aclaración", "En aclaración", TES, "doc.nota_aclaracion"),
    ("En aclaración", "Reanudar", "Recibida", REV, ""),
    ("En aclaración", "Reanudar", "Recibida", TES, ""),
    ("Revisada", "Aprobar", "Aprobada", TES, ""),
    # Deshacer: con "Confirmar recepción" de un clic y aplicable en bloque, un bloque mal seleccionado
    # necesita marcha atrás. Al volver a Recibida se borra el sello de recepción (facturas/eventos).
    ("Revisada", "Regresar a recibida", "Recibida", REV, ""),
    ("Revisada", "Regresar a recibida", "Recibida", TES, ""),
    ("Recibida", "Rechazar", "Rechazada", TES, "doc.nota_aclaracion"),
    ("En revisión", "Rechazar", "Rechazada", TES, "doc.nota_aclaracion"),
    ("Revisada", "Rechazar", "Rechazada", TES, "doc.nota_aclaracion"),
    ("Error de lectura", "Corregida", "Recibida", TES, "doc.grand_total"),
]


def _como_estan(wf):
    """Las transiciones guardadas, en la misma forma que TRANSICIONES, para poder compararlas."""
    return {(t.state, t.action, t.next_state, t.allowed, t.condition or "") for t in wf.transitions}


def cerrar_acciones_pendientes():
    """Cierra los `Workflow Action` abiertos de las facturas de compra.

    Frappe crea uno por documento con los roles que en ese momento podían moverlo
    (frappe/workflow/doctype/workflow_action/workflow_action.py::create_workflow_actions_for_roles,
    que corre SIEMPRE, aunque el flujo no mande correos). El registro guarda el ESTADO del documento,
    no la acción, así que al retirar una transición los que quedan abiertos prometen un permiso que
    ya no existe. Se cierran en vez de borrarse —no dejan hijos huérfanos y queda el rastro— y el
    siguiente guardado de la factura crea el que corresponda al flujo nuevo
    (`is_workflow_action_already_created` sólo mira los que están en 'Open')."""
    abiertas = frappe.get_all("Workflow Action",
                              filters={"reference_doctype": "Purchase Invoice", "status": "Open"},
                              pluck="name")
    for name in abiertas:
        frappe.db.set_value("Workflow Action", name, "status", "Completed", update_modified=False)
    return abiertas


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
    cambiaron = _como_estan(wf) != {tuple(t) for t in TRANSICIONES}
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
    if cambiaron:
        cerrar_acciones_pendientes()
