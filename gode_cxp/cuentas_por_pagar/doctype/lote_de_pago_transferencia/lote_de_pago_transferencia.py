"""Una transferencia del lote = una línea (registro 3) del archivo TEF.

Sin lógica propia: las reglas del lote completo viven en gode_cxp/pagos/eventos.py, que es donde se
puede validar una transferencia contra sus facturas.
"""
from frappe.model.document import Document


class LotedePagoTransferencia(Document):
    pass
