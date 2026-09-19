from frappe.model.document import Document


class ResultadoBancarioMovimiento(Document):
    """Una línea de lo que contestó el banco. Sin lógica propia: el cruce con el lote y los totales
    los calcula el hook validate del padre (gode_cxp.banamex.aplicar.validar_resultado)."""

    pass
