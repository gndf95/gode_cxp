"""Custom Fields sobre DocTypes estándar (idempotente vía create_custom_fields)."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ESTADOS_REVISION = "\nRecibida\nEn revisión\nEn aclaración\nRevisada\nAprobada\nRechazada\nError de lectura"

CAMPOS = {
    "Supplier": [
        {"fieldname": "sec_cxp", "label": "Cuentas por pagar", "fieldtype": "Section Break", "insert_after": "supplier_group"},
        {"fieldname": "tipo_persona", "label": "Tipo de persona", "fieldtype": "Select", "options": "\nFísica\nMoral", "insert_after": "sec_cxp"},
        {"fieldname": "nombre_pila", "label": "Nombre(s) (persona física)", "fieldtype": "Data", "insert_after": "tipo_persona", "depends_on": "eval:doc.tipo_persona=='Física'"},
        {"fieldname": "apellido_paterno", "label": "Apellido paterno", "fieldtype": "Data", "insert_after": "nombre_pila", "depends_on": "eval:doc.tipo_persona=='Física'"},
        {"fieldname": "apellido_materno", "label": "Apellido materno", "fieldtype": "Data", "insert_after": "apellido_paterno", "depends_on": "eval:doc.tipo_persona=='Física'"},
        {"fieldname": "col_cxp", "fieldtype": "Column Break", "insert_after": "apellido_materno"},
        {"fieldname": "correo_avisos", "label": "Correo para avisos de pago", "fieldtype": "Data", "options": "Email", "insert_after": "col_cxp"},
        {"fieldname": "bloqueado_pagos", "label": "Bloqueado para pagos", "fieldtype": "Check", "insert_after": "correo_avisos"},
        {"fieldname": "motivo_bloqueo", "label": "Motivo del bloqueo", "fieldtype": "Small Text", "insert_after": "bloqueado_pagos", "depends_on": "bloqueado_pagos"},
    ],
    "Purchase Invoice": [
        {"fieldname": "sec_cfdi", "label": "CFDI", "fieldtype": "Section Break", "insert_after": "bill_date", "collapsible": 0},
        {"fieldname": "cfdi_uuid", "label": "UUID (folio fiscal)", "fieldtype": "Data", "insert_after": "sec_cfdi", "unique": 1, "read_only": 1, "in_standard_filter": 1, "length": 36},
        {"fieldname": "cfdi_recibido", "label": "CFDI recibido", "fieldtype": "Link", "options": "CFDI Recibido", "insert_after": "cfdi_uuid", "read_only": 1},
        {"fieldname": "rfc_emisor", "label": "RFC emisor", "fieldtype": "Data", "insert_after": "cfdi_recibido", "read_only": 1},
        {"fieldname": "col_cfdi", "fieldtype": "Column Break", "insert_after": "rfc_emisor"},
        {"fieldname": "metodo_pago_sat", "label": "Método de pago SAT", "fieldtype": "Data", "insert_after": "col_cfdi", "read_only": 1},
        {"fieldname": "forma_pago_sat", "label": "Forma de pago SAT", "fieldtype": "Data", "insert_after": "metodo_pago_sat", "read_only": 1},
        {"fieldname": "complemento_recibido", "label": "Complemento de pago recibido", "fieldtype": "Check", "insert_after": "forma_pago_sat", "read_only": 1, "allow_on_submit": 1},
        {"fieldname": "sec_revision", "label": "Revisión", "fieldtype": "Section Break", "insert_after": "complemento_recibido"},
        {"fieldname": "estado_revision", "label": "Estado de revisión", "fieldtype": "Select", "options": ESTADOS_REVISION, "insert_after": "sec_revision", "read_only": 1, "in_list_view": 1, "in_standard_filter": 1, "allow_on_submit": 1},
        {"fieldname": "recepcion_confirmada", "label": "Recepción del producto/servicio confirmada", "fieldtype": "Check", "insert_after": "estado_revision"},
        {"fieldname": "recepcion_confirmada_por", "label": "Recepción confirmada por", "fieldtype": "Link", "options": "User", "insert_after": "recepcion_confirmada", "read_only": 1},
        {"fieldname": "recepcion_confirmada_el", "label": "Recepción confirmada el", "fieldtype": "Datetime", "insert_after": "recepcion_confirmada_por", "read_only": 1},
        {"fieldname": "col_revision", "fieldtype": "Column Break", "insert_after": "recepcion_confirmada_el"},
        {"fieldname": "nota_aclaracion", "label": "Nota de aclaración / rechazo", "fieldtype": "Text", "insert_after": "col_revision", "allow_on_submit": 1},
    ],
}


def asegurar_campos():
    create_custom_fields(CAMPOS, update=True)
    for dt in CAMPOS:
        frappe.clear_cache(doctype=dt)
