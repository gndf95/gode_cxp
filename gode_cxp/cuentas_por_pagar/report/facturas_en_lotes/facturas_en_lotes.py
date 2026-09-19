"""Reporte 'Facturas en lotes': facturas que un Lote de Pago sigue apartando (`en_lote`) y en qué
estado están tanto el lote como su transferencia.

Una factura sale de aquí en cuanto el lote que la apartaba queda Aplicado por completo y su saldo
se libera (pagos/eventos y banamex/aplicar la sueltan): esto es "dónde está atorado", no historial.
"""
import frappe


def execute(filters=None):
    filters = frappe._dict(filters or {})
    condiciones = ["pi.en_lote is not null", "pi.en_lote != ''"]
    parametros = {}
    if filters.get("proveedor"):
        condiciones.append("pi.supplier = %(proveedor)s")
        parametros["proveedor"] = filters.get("proveedor")
    if filters.get("estado_lote"):
        condiciones.append("lp.estado_lote = %(estado_lote)s")
        parametros["estado_lote"] = filters.get("estado_lote")
    datos = frappe.db.sql(f"""
        select
            pi.name as factura,
            pi.supplier as proveedor,
            pi.bill_no as folio,
            pi.posting_date as fecha,
            pi.due_date as vencimiento,
            pi.grand_total as total,
            pi.outstanding_amount as saldo,
            pi.en_lote as lote,
            lp.estado_lote as estado_lote,
            t.estado_pago as estado_transferencia,
            t.pago as pago
        from `tabPurchase Invoice` pi
        join `tabLote de Pago Factura` f
            on f.factura = pi.name and f.parent = pi.en_lote and f.parenttype = 'Lote de Pago'
        join `tabLote de Pago Transferencia` t
            on t.parent = f.parent and t.parenttype = 'Lote de Pago' and t.idx = f.transferencia
        join `tabLote de Pago` lp on lp.name = pi.en_lote
        where {" and ".join(condiciones)}
        order by pi.due_date, pi.name
    """, parametros, as_dict=True)
    return _columnas(), datos


def _columnas():
    return [
        {"label": "Factura", "fieldname": "factura", "fieldtype": "Link", "options": "Purchase Invoice", "width": 150},
        {"label": "Proveedor", "fieldname": "proveedor", "fieldtype": "Link", "options": "Supplier", "width": 160},
        {"label": "Folio", "fieldname": "folio", "fieldtype": "Data", "width": 90},
        {"label": "Fecha", "fieldname": "fecha", "fieldtype": "Date", "width": 100},
        {"label": "Vencimiento", "fieldname": "vencimiento", "fieldtype": "Date", "width": 100},
        {"label": "Total", "fieldname": "total", "fieldtype": "Currency", "width": 110},
        {"label": "Saldo", "fieldname": "saldo", "fieldtype": "Currency", "width": 110},
        {"label": "Lote", "fieldname": "lote", "fieldtype": "Link", "options": "Lote de Pago", "width": 150},
        {"label": "Estado del lote", "fieldname": "estado_lote", "fieldtype": "Data", "width": 110},
        {"label": "Transferencia", "fieldname": "estado_transferencia", "fieldtype": "Data", "width": 110},
        {"label": "Pago", "fieldname": "pago", "fieldtype": "Link", "options": "Payment Entry", "width": 150},
    ]
