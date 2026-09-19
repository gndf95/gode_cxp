"""Puntos de entrada del escritorio para el resultado del banco.

Sin lógica: comprueba el permiso y delega en banamex.aplicar, igual que pagos/api.py. El permiso se
pide aquí y no sólo en el DocType porque estas llamadas deciden qué facturas se dan por pagadas, y el
permiso de escritura del documento no alcanza para distinguirlas de guardar una nota.
"""
import frappe
from frappe import _

from gode_cxp.banamex import aplicar


def _exigir(*roles):
    if not set(roles) & set(frappe.get_roles()):
        frappe.throw(_("No tienes permiso para esta operación (se necesita {0}).").format(" o ".join(roles)),
                     frappe.PermissionError)


def _exigir_el_lote(lote):
    """Además del rol, el permiso de escritura SOBRE EL LOTE.

    El rol es global; las User Permissions (por empresa, por ejemplo) sólo se aplican mirando el
    documento, y eso lo hace `check_permission`. Sin esto, quien tuviera el rol de Tesorería podía
    capturar el resultado del lote de una empresa que no le toca."""
    frappe.get_doc("Lote de Pago", lote).check_permission("write")


def _exigir_el_resultado(resultado):
    """Permiso sobre el resultado Y sobre su lote: el resultado es sólo la hoja donde se apunta lo que
    el banco hizo con el dinero del lote."""
    doc = frappe.get_doc("Resultado Bancario", resultado)
    doc.check_permission("write")
    _exigir_el_lote(doc.lote)
    return doc


@frappe.whitelist()
def crear_captura_manual(lote):
    """Alta del resultado con un movimiento por transferencia (el botón del formulario del lote)."""
    _exigir("CxP Tesoreria", "System Manager")
    _exigir_el_lote(lote)
    return aplicar.crear_resultado_desde_lote(lote).name


@frappe.whitelist()
def importar_respuesta(resultado, file_url):
    """Carga el archivo que devolvió BancaNet. Devuelve un resumen para la pantalla."""
    _exigir("CxP Tesoreria", "System Manager")
    _exigir_el_resultado(resultado)
    r = aplicar.cargar_archivo(resultado, file_url)
    resumen = _("{0} movimientos: {1} aplicados y {2} rechazados.").format(
        len(r.movimientos), r.num_aplicados, r.num_rechazados)
    if r.diferencias:
        resumen += "\n" + _("Diferencias con el lote:") + "\n" + r.diferencias
    return {"name": r.name, "resumen": resumen}


@frappe.whitelist()
def marcar_revisado(resultado):
    """El visto bueno de Tesorería a un resultado con diferencias.

    `estado` es read_only en el DocType (lo mueven la carga del archivo y la creación de los pagos),
    así que el botón pasa por aquí y se escribe con `db_set`: un `save()` volvería a correr el hook
    validate, que lo regresaría a 'Con diferencias'."""
    _exigir("CxP Tesoreria", "System Manager")
    doc = _exigir_el_resultado(resultado)
    if doc.estado != "Con diferencias":
        frappe.throw(_("Solo se marca como revisado un resultado con diferencias (el {0} está en '{1}')."
                       ).format(doc.name, doc.estado))
    doc.db_set("estado", "Revisado")
    return doc.name
