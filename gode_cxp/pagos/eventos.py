"""Reglas del Lote de Pago: qué puede entrar y qué pasa con las facturas al autorizar y cancelar.

Van como doc_events (hooks.py) y no como métodos del controlador para que corran igual si el lote se
guarda a mano desde el formulario y si lo arma pagos/lotes.py.
"""
import frappe
from frappe import _
from frappe.utils import flt

from gode_cxp.pagos.cuentas_bancarias import validar_nombre_tef
from gode_cxp.pagos.lotes import ACTIVOS


CAMPOS_DE_ARCHIVO = ("secuencial", "nombre_archivo", "archivo_tef", "generado_el", "transmitido_el",
                     "autorizacion_banco")
CAMPOS_DE_PAGO = ("linea_tef", "pago", "clave_rastreo", "motivo_rechazo", "reintentado_en")


def _nace_limpio(doc):
    """Un lote nuevo no hereda nada de lo que ya se mandó al banco.

    Los campos llevan `no_copy`, pero el botón 'Amend' del escritorio lo ignora
    (frappe/public/js/frappe/model/create_new.js: `!from_amend && df.no_copy`), así que la enmienda de
    un lote cancelado llegaría con su secuencial, su archivo y su estado. Misma técnica que
    facturas/eventos.py con las enmiendas de factura."""
    for campo in CAMPOS_DE_ARCHIVO:
        doc.set(campo, None)
    doc.estado_lote = "Preparado"
    # `lote_origen` sólo se limpia en una enmienda: nuevo_lote_pendientes lo pone a propósito en el
    # lote de reintento y es el único rastro de dónde viene.
    if doc.get("amended_from"):
        doc.lote_origen = None
    for t in doc.transferencias:
        t.estado_pago = "Pendiente"
        for campo in CAMPOS_DE_PAGO:
            t.set(campo, None)


def validar_lote(doc, method=None):
    if doc.is_new():
        _nace_limpio(doc)
    if doc.naturaleza not in ("06", "12"):
        frappe.throw(_("El lote necesita naturaleza 06 o 12."))
    if not doc.transferencias or not doc.facturas:
        frappe.throw(_("El lote necesita al menos una transferencia con facturas."))
    idx_validos = {t.idx for t in doc.transferencias}
    suma_por_transferencia = {}
    suma_por_factura = {}
    for f in doc.facturas:
        # Una factura sólo puede aparecer UNA vez: dos filas de la misma factura se comparaban cada
        # una contra el saldo completo, así que dos veces 600 de una factura de 1000 pasaban.
        if f.factura in suma_por_factura:
            frappe.throw(_("La factura {0} aparece dos veces en el lote: junta lo que le vas a pagar "
                           "en una sola fila.").format(f.factura))
        suma_por_factura[f.factura] = flt(f.importe)
        if f.transferencia not in idx_validos:
            frappe.throw(_("La factura {0} apunta a una transferencia inexistente.").format(f.factura))
        pi = frappe.db.get_value("Purchase Invoice", f.factura,
                                 ["docstatus", "estado_revision", "on_hold", "outstanding_amount", "currency",
                                  "supplier", "en_lote", "company"], as_dict=True)
        if not pi or pi.docstatus != 1 or pi.estado_revision != "Aprobada" or pi.on_hold:
            frappe.throw(_("La factura {0} no está aprobada (o está en espera).").format(f.factura))
        if pi.currency != "MXN" or pi.company != doc.company:
            frappe.throw(_("La factura {0} no es MXN de {1}.").format(f.factura, doc.company))
        if flt(suma_por_factura[f.factura]) <= 0 or flt(suma_por_factura[f.factura]) > flt(pi.outstanding_amount) + 0.005:
            frappe.throw(_("Importe inválido para {0}: {1} (saldo {2}).")
                         .format(f.factura, suma_por_factura[f.factura], pi.outstanding_amount))
        if pi.en_lote and pi.en_lote != doc.name:
            frappe.throw(_("La factura {0} ya está en el lote {1}.").format(f.factura, pi.en_lote))
        # Un lote Preparado (borrador) todavía no escribió en_lote, así que hay que mirar sus filas.
        otro = frappe.db.sql("""select lf.parent from `tabLote de Pago Factura` lf
                                join `tabLote de Pago` l on l.name = lf.parent
                                where lf.factura = %s and lf.parent != %s and l.estado_lote in %s""",
                             (f.factura, doc.name or "", ACTIVOS))
        if otro:
            frappe.throw(_("La factura {0} ya está en el lote {1}.").format(f.factura, otro[0][0]))
        if frappe.db.get_value("Supplier", pi.supplier, "bloqueado_pagos"):
            frappe.throw(_("El proveedor {0} está bloqueado para pagos.").format(pi.supplier))
        suma_por_transferencia[f.transferencia] = suma_por_transferencia.get(f.transferencia, 0) + flt(f.importe)
    for t in doc.transferencias:
        c = frappe.db.get_value("Bank Account", t.cuenta_bancaria,
                                ["verificada", "tipo_pago_tef", "party", "disabled"], as_dict=True)
        if not c or c.party != t.proveedor or c.disabled:
            frappe.throw(_("La cuenta {0} no es del proveedor {1}.").format(t.cuenta_bancaria, t.proveedor))
        if not c.verificada:
            frappe.throw(_("La cuenta {0} de {1} no está verificada por Tesorería.").format(t.cuenta_bancaria, t.proveedor))
        if c.tipo_pago_tef != doc.naturaleza:
            frappe.throw(_("La cuenta de {0} es naturaleza {1}; el lote es {2}.").format(t.proveedor, c.tipo_pago_tef, doc.naturaleza))
        error = validar_nombre_tef(t.beneficiario_tef)
        if error:
            frappe.throw(_("{0}: {1}.").format(t.proveedor, error))
        if abs(flt(t.importe) - suma_por_transferencia.get(t.idx, 0)) > 0.005:
            frappe.throw(_("La transferencia de {0} ({1}) no cuadra con sus facturas ({2}).")
                         .format(t.proveedor, t.importe, suma_por_transferencia.get(t.idx, 0)))
    doc.total_lote = sum(flt(t.importe) for t in doc.transferencias)
    doc.num_transferencias = len(doc.transferencias)
    if doc.docstatus == 0:
        doc.estado_lote = "Preparado"


def al_autorizar(doc, method=None):
    """Submit = Tesorería autoriza. Aquí se apartan las facturas (en_lote); el saldo no se toca."""
    doc.db_set("estado_lote", "Autorizado")
    for f in doc.facturas:
        frappe.db.set_value("Purchase Invoice", f.factura, "en_lote", doc.name)


def antes_de_cancelar(doc, method=None):
    if doc.estado_lote in ("Transmitido", "Aplicado", "Parcial"):
        frappe.throw(_("Un lote ya transmitido al banco no se cancela; usa 'Nuevo lote con los pendientes'."))


def al_cancelar(doc, method=None):
    doc.db_set("estado_lote", "Cancelado")
    for f in doc.facturas:
        if frappe.db.get_value("Purchase Invoice", f.factura, "en_lote") == doc.name:
            frappe.db.set_value("Purchase Invoice", f.factura, "en_lote", None)
