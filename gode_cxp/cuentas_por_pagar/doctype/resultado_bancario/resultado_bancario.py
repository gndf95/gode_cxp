from frappe.model.document import Document


class ResultadoBancario(Document):
    """Lo que el banco contestó sobre un lote de pago.

    Sin lógica en el controlador a propósito: los totales, el cruce con el lote y las diferencias van
    en el hook `validate` (gode_cxp.banamex.aplicar.validar_resultado) para que corran igual si el
    resultado se captura desde el formulario y si lo arma banamex/aplicar.py."""

    pass
