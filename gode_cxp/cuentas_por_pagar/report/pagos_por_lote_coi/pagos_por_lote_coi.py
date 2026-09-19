"""Reporte 'Pagos por lote COI': una fila por (transferencia, factura) de los lotes ya transmitidos
al banco, para que Contabilidad revise qué se pagó, con qué autorización y qué clave de rastreo.

Los filtros nunca se interpolan en el SQL: siempre van como parámetros nombrados.
"""
import frappe
from frappe.utils import cint


def execute(filters=None):
    filters = frappe._dict(filters or {})
    condiciones = ["lp.docstatus = 1", "lp.fecha_pago between %(desde)s and %(hasta)s"]
    parametros = {"desde": filters.get("desde"), "hasta": filters.get("hasta")}
    if filters.get("lote"):
        condiciones.append("lp.name = %(lote)s")
        parametros["lote"] = filters.get("lote")
    # Por omisión sólo lo que el banco aplicó (estado_pago = 'Aplicado'); solo_aplicados=0 trae todo.
    if cint(filters.get("solo_aplicados", 1)):
        condiciones.append("t.estado_pago = 'Aplicado'")
    datos = frappe.db.sql(f"""
        select
            lp.name as lote,
            lp.fecha_pago as fecha_pago,
            lp.naturaleza as naturaleza,
            lp.secuencial as secuencial,
            lp.autorizacion_banco as autorizacion,
            t.linea_tef as linea,
            t.proveedor as proveedor,
            sup.tax_id as rfc,
            t.beneficiario_tef as beneficiario_tef,
            t.importe as importe_transferido,
            t.estado_pago as estado_pago,
            t.clave_rastreo as clave_rastreo,
            t.pago as pago,
            f.factura as factura,
            pi.bill_no as folio,
            f.uuid as uuid,
            f.importe as importe_aplicado
        from `tabLote de Pago` lp
        join `tabLote de Pago Transferencia` t
            on t.parent = lp.name and t.parenttype = 'Lote de Pago'
        join `tabLote de Pago Factura` f
            on f.parent = lp.name and f.parenttype = 'Lote de Pago' and f.transferencia = t.idx
        join `tabPurchase Invoice` pi on pi.name = f.factura
        left join `tabSupplier` sup on sup.name = t.proveedor
        where {" and ".join(condiciones)}
        order by lp.fecha_pago, lp.name, t.idx, f.idx
    """, parametros, as_dict=True)
    return _columnas(), datos


def _columnas():
    return [
        {"label": "Lote", "fieldname": "lote", "fieldtype": "Link", "options": "Lote de Pago", "width": 150},
        {"label": "Fecha de pago", "fieldname": "fecha_pago", "fieldtype": "Date", "width": 100},
        {"label": "Naturaleza", "fieldname": "naturaleza", "fieldtype": "Data", "width": 90},
        {"label": "Secuencial", "fieldname": "secuencial", "fieldtype": "Int", "width": 90},
        {"label": "Autorización", "fieldname": "autorizacion", "fieldtype": "Data", "width": 110},
        {"label": "Línea", "fieldname": "linea", "fieldtype": "Int", "width": 70},
        {"label": "Proveedor", "fieldname": "proveedor", "fieldtype": "Link", "options": "Supplier", "width": 160},
        {"label": "RFC", "fieldname": "rfc", "fieldtype": "Data", "width": 110},
        {"label": "Beneficiario TEF", "fieldname": "beneficiario_tef", "fieldtype": "Data", "width": 160},
        {"label": "Importe transferido", "fieldname": "importe_transferido", "fieldtype": "Currency", "width": 130},
        {"label": "Estado del pago", "fieldname": "estado_pago", "fieldtype": "Data", "width": 110},
        {"label": "Clave de rastreo", "fieldname": "clave_rastreo", "fieldtype": "Data", "width": 130},
        {"label": "Pago", "fieldname": "pago", "fieldtype": "Link", "options": "Payment Entry", "width": 150},
        {"label": "Factura", "fieldname": "factura", "fieldtype": "Link", "options": "Purchase Invoice", "width": 150},
        {"label": "Folio", "fieldname": "folio", "fieldtype": "Data", "width": 90},
        {"label": "UUID", "fieldname": "uuid", "fieldtype": "Data", "width": 200},
        {"label": "Importe aplicado a la factura", "fieldname": "importe_aplicado", "fieldtype": "Currency", "width": 150},
    ]
