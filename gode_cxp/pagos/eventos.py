"""Reglas del Lote de Pago: qué puede entrar y qué pasa con las facturas al autorizar y cancelar.

Van como doc_events (hooks.py) y no como métodos del controlador para que corran igual si el lote se
guarda a mano desde el formulario y si lo arma pagos/lotes.py.
"""
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import cstr, flt, get_datetime

from gode_cxp.pagos.cuentas_bancarias import REGISTRADA, SIN_PREREGISTRO, validar_nombre_tef
from gode_cxp.pagos.lotes import ACTIVOS, REINTENTABLES, bloquear_facturas
from gode_cxp.pagos.preregistro import exigir_preregistro


CAMPOS_DE_ARCHIVO = ("secuencial", "nombre_archivo", "archivo_tef", "generado_el", "transmitido_el",
                     "autorizacion_banco")
CAMPOS_DE_PAGO = ("linea_tef", "pago", "clave_rastreo", "motivo_rechazo", "reintentado_en")
# Sólo las notas y la auditoría de Frappe pueden cambiar al guardar un lote enviado.
EDITABLES = {"notas", "modified", "modified_by", "_user_tags", "_comments", "_assign", "_liked_by"}
# Estados desde los que sí se cancela: el archivo puede estar generado, pero no se subió al banco.
CANCELABLES = ("Autorizado", "Exportado")


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


def _cambio(antes, ahora, tipo=None):
    """Normaliza fechas del navegador sin tolerar cambios pequeños en campos protegidos."""
    if tipo in ("Date", "Datetime") and antes and ahora:
        try:
            return get_datetime(antes) != get_datetime(ahora)
        except (ValueError, TypeError, OverflowError):
            return True
    if isinstance(antes, int | float | Decimal) or isinstance(ahora, int | float | Decimal):
        # flt convierte texto inválido a cero; aquí eso abriría un campo protegido cuyo valor era 0.
        try:
            return Decimal(cstr(antes) or "0") != Decimal(cstr(ahora) or "0")
        except InvalidOperation:
            return True
    return cstr(antes) != cstr(ahora)


def _validar_campos_blindados(antes, ahora, tablas=()):
    # La unión incluye campos nuevos del DocType: ninguno queda abierto por omisión.
    campos = set(antes.as_dict()) | set(ahora.as_dict())
    for campo in campos - EDITABLES - set(tablas):
        df = ahora.meta.get_field(campo)
        if _cambio(antes.get(campo), ahora.get(campo), df.fieldtype if df else None):
            frappe.throw(_("'{0}' de un lote autorizado no se edita a mano.")
                         .format(ahora.meta.get_label(campo) or campo))


def validar_cambios_del_lote_enviado(doc, method=None):
    """El formulario no protege el dinero: esta guardia también cubre guardados por API.

    Los botones internos usan db_set; no necesitan saltarse la guardia de save().
    """
    antes = doc.get_doc_before_save()
    if not antes:
        frappe.throw(_("No se pudo leer el lote anterior: no se permiten cambios al lote autorizado."))
    tablas = ("transferencias", "facturas")
    _validar_campos_blindados(antes, doc, tablas)
    for tabla in tablas:
        if not antes.get(tabla) or not doc.get(tabla):
            frappe.throw(_("No se pueden validar las filas de {0} del lote autorizado.").format(tabla))
        previas = {fila.name: fila for fila in antes.get(tabla)}
        actuales = {fila.name: fila for fila in doc.get(tabla)}
        if (not all(previas) or not all(actuales) or set(previas) != set(actuales)
                or len(previas) != len(antes.get(tabla)) or len(actuales) != len(doc.get(tabla))):
            frappe.throw(_("A un lote autorizado no se le agregan ni se le quitan filas de {0}.").format(tabla))
        for nombre, fila in actuales.items():
            # idx también queda blindado: cambiarlo reasignaría facturas a otra transferencia.
            _validar_campos_blindados(previas[nombre], fila)


def _facturas_reintentables(lote_origen, este_lote) -> set[str]:
    if not lote_origen:
        return set()
    # Una lectura actual incluye tanto el resultado del banco como el destino del reintento.
    filas = frappe.db.sql("""select lf.factura from `tabLote de Pago Factura` lf
                            join `tabLote de Pago Transferencia` lt
                              on lf.parent = lt.parent and lf.transferencia = lt.idx
                            join `tabLote de Pago` l on l.name = lf.parent
                            where lf.parent = %s and l.estado_lote in %s
                              and lt.estado_pago in ('Pendiente', 'Rechazado', 'Devuelto')
                              and (coalesce(lt.reintentado_en, '') = '' or lt.reintentado_en = %s)
                            for update""", (lote_origen, REINTENTABLES, este_lote or ""))
    return {fila[0] for fila in filas}


def validar_lote(doc, method=None):
    if doc.is_new():
        if doc.lote_origen and not doc.flags.reintento_interno:
            frappe.throw(_("El lote origen sólo se asigna desde Nuevo lote con los pendientes."))
        _nace_limpio(doc)
    elif cstr(doc.lote_origen) != cstr(frappe.db.get_value("Lote de Pago", doc.name, "lote_origen")):
        frappe.throw(_("El lote origen no se modifica a mano."))
    if doc.naturaleza not in ("06", "12"):
        frappe.throw(_("El lote necesita naturaleza 06 o 12."))
    if not doc.transferencias or not doc.facturas:
        frappe.throw(_("El lote necesita al menos una transferencia con facturas."))
    # FOR UPDATE devuelve la versión actual; get_value volvería al snapshot de REPEATABLE READ.
    facturas = bloquear_facturas([f.factura for f in doc.facturas])
    reintentables = _facturas_reintentables(doc.lote_origen, doc.name)
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
        pi = facturas.get(f.factura)
        if not pi or pi.docstatus != 1 or pi.estado_revision != "Aprobada" or pi.on_hold:
            frappe.throw(_("La factura {0} no está aprobada (o está en espera).").format(f.factura))
        if pi.currency != "MXN" or pi.company != doc.company:
            frappe.throw(_("La factura {0} no es MXN de {1}.").format(f.factura, doc.company))
        if flt(suma_por_factura[f.factura]) <= 0 or flt(suma_por_factura[f.factura]) > flt(pi.outstanding_amount) + 0.005:
            frappe.throw(_("Importe inválido para {0}: {1} (saldo {2}).")
                         .format(f.factura, suma_por_factura[f.factura], pi.outstanding_amount))
        if pi.en_lote and pi.en_lote != doc.name and not (pi.en_lote == doc.lote_origen and f.factura in reintentables):
            frappe.throw(_("La factura {0} ya está en el lote {1}.").format(f.factura, pi.en_lote))
        # Los borradores aún no escriben en_lote. El join lee con candado para ver lotes
        # recién confirmados y no el snapshot anterior de REPEATABLE READ.
        otro = frappe.db.sql("""select lf.parent from `tabLote de Pago Factura` lf
                                join `tabLote de Pago` l on l.name = lf.parent
                                where lf.factura = %s and lf.parent != %s and l.estado_lote in %s for update""",
                             (f.factura, doc.name or "", ACTIVOS))
        if otro:
            frappe.throw(_("La factura {0} ya está en el lote {1}.").format(f.factura, otro[0][0]))
        if frappe.db.get_value("Supplier", pi.supplier, "bloqueado_pagos"):
            frappe.throw(_("El proveedor {0} está bloqueado para pagos.").format(pi.supplier))
        suma_por_transferencia[f.transferencia] = suma_por_transferencia.get(f.transferencia, 0) + flt(f.importe)
    # Una sola lectura de la configuración para todas las transferencias del lote.
    exigir = exigir_preregistro()
    for t in doc.transferencias:
        c = frappe.db.get_value("Bank Account", t.cuenta_bancaria,
                                ["verificada", "tipo_pago_tef", "party", "disabled", "estado_preregistro"],
                                as_dict=True)
        if not c or c.party != t.proveedor or c.disabled:
            frappe.throw(_("La cuenta {0} no es del proveedor {1}.").format(t.cuenta_bancaria, t.proveedor))
        if not c.verificada:
            frappe.throw(_("La cuenta {0} de {1} no está verificada por Tesorería.").format(t.cuenta_bancaria, t.proveedor))
        # Banamex rechaza la transferencia a una cuenta que no está dada de alta en el contrato: el
        # archivo se subiría y el pago no saldría, así que se frena antes de armar el lote.
        if exigir and c.estado_preregistro != REGISTRADA:
            frappe.throw(_("La cuenta de {0} no está pre-registrada en BancaNet (estado: {1}).")
                         .format(t.proveedor, c.estado_preregistro or SIN_PREREGISTRO))
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
    # Se vuelve a tomar el candado y a comprobar `en_lote` DESPUÉS de tenerlo: si otra sesión apartó
    # alguna factura, el submit se aborta antes de escribir nada.
    facturas = bloquear_facturas([f.factura for f in doc.facturas])
    reintentables = _facturas_reintentables(doc.lote_origen, doc.name)
    for f in doc.facturas:
        pi = facturas.get(f.factura)
        if not pi:
            frappe.throw(_("No existe la factura {0}.").format(f.factura))
        en_lote = pi.en_lote
        if en_lote and en_lote != doc.name and not (en_lote == doc.lote_origen and f.factura in reintentables):
            frappe.throw(_("La factura {0} la acaba de apartar el lote {1}: vuelve a armar este lote.")
                         .format(f.factura, en_lote))
    doc.db_set("estado_lote", "Autorizado")
    for f in doc.facturas:
        frappe.db.set_value("Purchase Invoice", f.factura, "en_lote", doc.name)


def antes_de_cancelar(doc, method=None):
    """Cancelar libera las facturas, así que sólo se cancela un lote que NO se subió al banco.

    'Preparado' es borrador y no llega aquí; Transmitido, Aplicado, Parcial y Rechazado ya se fueron
    a BancaNet y su camino es 'Nuevo lote con los pendientes'."""
    if doc.estado_lote not in CANCELABLES:
        frappe.throw(_("Un lote en estado '{0}' no se cancela: sólo se cancela un lote Autorizado o "
                       "Exportado que todavía no se subió al banco. Si ya se transmitió, usa 'Nuevo "
                       "lote con los pendientes'.").format(doc.estado_lote))


def al_cancelar(doc, method=None):
    doc.db_set("estado_lote", "Cancelado")
    for f in doc.facturas:
        if frappe.db.get_value("Purchase Invoice", f.factura, "en_lote") == doc.name:
            frappe.db.set_value("Purchase Invoice", f.factura, "en_lote", None)


def antes_de_borrar(doc, method=None):
    # Incluso cancelado conserva su secuencial: borrarlo permitiría reutilizarlo.
    # frappe.flags vive solo en el servidor: la limpieza de las pruebas (pruebas_comun.limpiar, que además
    # exige estar en la empresa de pruebas) es la única que lo prende.
    if frappe.flags.cxp_limpiando_pruebas:
        return
    if doc.docstatus != 0:
        frappe.throw(_("Sólo se pueden borrar lotes en borrador; los enviados y cancelados conservan su historial."))
