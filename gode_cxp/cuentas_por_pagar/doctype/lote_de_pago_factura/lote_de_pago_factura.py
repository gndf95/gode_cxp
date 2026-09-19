"""Qué factura y con cuánto va cubierta por cada transferencia del lote.

Sin lógica propia: las reglas viven en gode_cxp/pagos/eventos.py.
"""
from frappe.model.document import Document


class LotedePagoFactura(Document):
    pass
