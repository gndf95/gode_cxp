"""Custom Fields sobre DocTypes estándar (idempotente vía create_custom_fields)."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ESTADOS_REVISION = "\nRecibida\nEn revisión\nEn aclaración\nRevisada\nAprobada\nRechazada\nError de lectura"

# El sello del CFDI y la revisión llevan no_copy: duplicar una factura no puede arrastrar el UUID
# (índice único) ni el estado ya aprobado. Ojo: al ENMENDAR, el escritorio copia hasta los campos
# no_copy (frappe/public/js/frappe/model/create_new.js: no_copy sólo se respeta si la copia no viene
# de un amend), así que la enmienda la limpia además facturas/eventos.py.

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
        {"fieldname": "cfdi_uuid", "label": "UUID (folio fiscal)", "fieldtype": "Data", "insert_after": "sec_cfdi", "unique": 1, "read_only": 1, "in_standard_filter": 1, "length": 36, "no_copy": 1},
        {"fieldname": "cfdi_recibido", "label": "CFDI recibido", "fieldtype": "Link", "options": "CFDI Recibido", "insert_after": "cfdi_uuid", "read_only": 1, "no_copy": 1},
        {"fieldname": "rfc_emisor", "label": "RFC emisor", "fieldtype": "Data", "insert_after": "cfdi_recibido", "read_only": 1},
        {"fieldname": "col_cfdi", "fieldtype": "Column Break", "insert_after": "rfc_emisor"},
        {"fieldname": "metodo_pago_sat", "label": "Método de pago SAT", "fieldtype": "Data", "insert_after": "col_cfdi", "read_only": 1},
        {"fieldname": "forma_pago_sat", "label": "Forma de pago SAT", "fieldtype": "Data", "insert_after": "metodo_pago_sat", "read_only": 1},
        {"fieldname": "complemento_recibido", "label": "Complemento de pago recibido", "fieldtype": "Check", "insert_after": "forma_pago_sat", "read_only": 1, "allow_on_submit": 1},
        {"fieldname": "sec_revision", "label": "Revisión", "fieldtype": "Section Break", "insert_after": "complemento_recibido"},
        {"fieldname": "estado_revision", "label": "Estado de revisión", "fieldtype": "Select", "options": ESTADOS_REVISION, "insert_after": "sec_revision", "read_only": 1, "in_list_view": 1, "in_standard_filter": 1, "allow_on_submit": 1, "no_copy": 1},
        {"fieldname": "recepcion_confirmada", "label": "Recepción del producto/servicio confirmada", "fieldtype": "Check", "insert_after": "estado_revision", "no_copy": 1},
        {"fieldname": "recepcion_confirmada_por", "label": "Recepción confirmada por", "fieldtype": "Link", "options": "User", "insert_after": "recepcion_confirmada", "read_only": 1, "no_copy": 1},
        {"fieldname": "recepcion_confirmada_el", "label": "Recepción confirmada el", "fieldtype": "Datetime", "insert_after": "recepcion_confirmada_por", "read_only": 1, "no_copy": 1},
        {"fieldname": "col_revision", "fieldtype": "Column Break", "insert_after": "recepcion_confirmada_el"},
        {"fieldname": "nota_aclaracion", "label": "Nota de aclaración / rechazo", "fieldtype": "Text", "insert_after": "col_revision", "allow_on_submit": 1, "no_copy": 1},
        # En qué lote de pago entró la factura. Se llena al armar el lote y se limpia al cancelarlo,
        # así que va allow_on_submit (la factura ya está confirmada) y no_copy (una enmienda o una
        # copia no puede heredar el lote de otra factura).
        {"fieldname": "en_lote", "label": "En lote de pago", "fieldtype": "Link", "options": "Lote de Pago", "read_only": 1, "allow_on_submit": 1,
         "no_copy": 1, "in_standard_filter": 1, "insert_after": "nota_aclaracion"},
    ],
    # Los datos que el archivo TEF de Banamex necesita de cada cuenta. La CLABE va en campo propio y
    # no en el `iban` estándar: ERPNext valida el IBAN con el algoritmo europeo y la CLABE no lo pasa.
    "Bank Account": [
        {"fieldname": "sec_tef", "label": "Datos para TEF Banamex", "fieldtype": "Section Break", "insert_after": "bank_account_no",
         "depends_on": "eval:doc.party_type=='Supplier'"},
        {"fieldname": "clabe", "label": "CLABE (18 dígitos)", "fieldtype": "Data", "length": 18, "insert_after": "sec_tef", "in_list_view": 1},
        {"fieldname": "tipo_pago_tef", "label": "Naturaleza TEF", "fieldtype": "Select", "options": "\n06\n12", "read_only": 1, "insert_after": "clabe",
         "description": "06 = cuenta Banamex (sucursal + cuenta); 12 = interbancario por CLABE"},
        {"fieldname": "sucursal_banamex", "label": "Sucursal Banamex (4)", "fieldtype": "Data", "length": 4, "insert_after": "tipo_pago_tef", "depends_on": "eval:doc.tipo_pago_tef=='06'"},
        {"fieldname": "cuenta_banamex", "label": "Cuenta Banamex (7)", "fieldtype": "Data", "length": 7, "insert_after": "sucursal_banamex", "depends_on": "eval:doc.tipo_pago_tef=='06'"},
        {"fieldname": "col_tef", "fieldtype": "Column Break", "insert_after": "cuenta_banamex"},
        {"fieldname": "nombre_tef", "label": "Beneficiario en el archivo (55)", "fieldtype": "Data", "length": 55, "insert_after": "col_tef",
         "description": "Mayúsculas sin acentos. Física: NOMBRES,PATERNO/MATERNO · Moral: PRIMERA,RESTO DE LA RAZON SOCIAL/"},
        {"fieldname": "verificada", "label": "Verificada por Tesorería", "fieldtype": "Check", "read_only": 1, "insert_after": "nombre_tef", "in_list_view": 1},
        {"fieldname": "verificada_por", "label": "Verificada por", "fieldtype": "Link", "options": "User", "read_only": 1, "insert_after": "verificada"},
        {"fieldname": "verificada_el", "label": "Verificada el", "fieldtype": "Datetime", "read_only": 1, "insert_after": "verificada_por"},
    ],
    # El pago se crea al aplicar el resultado del banco: el lote, la autorización y la clave de
    # rastreo se escriben sobre el Payment Entry ya confirmado, de ahí el allow_on_submit.
    "Payment Entry": [
        {"fieldname": "sec_lote", "label": "Lote de pago Banamex", "fieldtype": "Section Break", "insert_after": "reference_date", "collapsible": 1},
        {"fieldname": "lote_pago", "label": "Lote de pago", "fieldtype": "Link", "options": "Lote de Pago", "read_only": 1, "allow_on_submit": 1, "insert_after": "sec_lote", "in_standard_filter": 1},
        {"fieldname": "autorizacion_banco", "label": "Autorización del banco", "fieldtype": "Data", "allow_on_submit": 1, "insert_after": "lote_pago"},
        {"fieldname": "col_lote", "fieldtype": "Column Break", "insert_after": "autorizacion_banco"},
        {"fieldname": "clave_rastreo", "label": "Clave de rastreo", "fieldtype": "Data", "allow_on_submit": 1, "insert_after": "col_lote"},
        {"fieldname": "comprobante", "label": "Comprobante del banco", "fieldtype": "Attach", "allow_on_submit": 1, "insert_after": "clave_rastreo"},
    ],
}


def asegurar_campos():
    create_custom_fields(CAMPOS, update=True)
    for dt in CAMPOS:
        frappe.clear_cache(doctype=dt)
