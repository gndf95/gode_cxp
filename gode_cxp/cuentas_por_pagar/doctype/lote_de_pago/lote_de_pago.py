"""Lote de pago Banamex.

Por ahora es el esqueleto que necesitan los campos `Payment Entry.lote_pago` y
`Purchase Invoice.en_lote` (que apuntan aquí con un Link). Las transferencias, las facturas que
cubre y la lógica de validate/on_submit/on_cancel llegan en la Task 4 del plan.
"""
from frappe.model.document import Document


class LotedePago(Document):
    pass
