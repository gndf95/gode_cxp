"""Custom Fields sobre DocTypes estándar (idempotente vía create_custom_fields)."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ESTADOS_REVISION = "\nRecibida\nEn revisión\nEn aclaración\nRevisada\nAprobada\nRechazada\nError de lectura"

# El sello del CFDI y la revisión llevan no_copy: duplicar una factura no puede arrastrar el UUID
# (índice único) ni el estado ya aprobado. Ojo: al ENMENDAR, el escritorio copia hasta los campos
# no_copy (frappe/public/js/frappe/model/create_new.js: no_copy sólo se respeta si la copia no viene
# de un amend), así que la enmienda la limpia además facturas/eventos.py.

CAMPOS = {
    # `sec_cxp` va después de `image` y no de `supplier_group`: un Section Break se lleva consigo
    # todo lo que venga después en el meta hasta el siguiente corte, y en Supplier v15 el orden es
    # supplier_group → supplier_type → is_transporter → image → defaults_section. Colgada de
    # `supplier_group`, la sección se tragaba esos tres campos estándar de ERPNext (lo vigila
    # test_produccion_pagos.test_las_secciones_nuevas_no_se_tragan_campos_estandar).
    "Supplier": [
        {"fieldname": "sec_cxp", "label": "Cuentas por pagar", "fieldtype": "Section Break", "insert_after": "image"},
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
    # `bank_account_no` es el ÚLTIMO campo de la sección estándar `account_details_section` (después
    # viene `address_and_contact`, otro Section Break), así que colgar `sec_tef` de él no esconde
    # ningún campo de ERPNext detrás del `depends_on`. Si una versión futura mete campos nuevos ahí,
    # hay que mover este `insert_after` al nuevo último campo (lo vigila
    # test_produccion_pagos.test_las_secciones_nuevas_no_se_tragan_campos_estandar).
    "Bank Account": [
        {"fieldname": "sec_tef", "label": "Datos para TEF Banamex", "fieldtype": "Section Break", "insert_after": "bank_account_no",
         "depends_on": "eval:doc.party_type=='Supplier'"},
        # La CLABE sale de la lista (in_list_view 0): es el número de cuenta del proveedor, se ve en
        # la ficha, y la columna que de verdad hace falta de un vistazo es la naturaleza del pago.
        # Las columnas de la lista las fija setup/instalar.asegurar_lista_cuentas_bancarias.
        {"fieldname": "clabe", "label": "CLABE (18 dígitos)", "fieldtype": "Data", "length": 18, "insert_after": "sec_tef", "in_list_view": 0},
        {"fieldname": "tipo_pago_tef", "label": "Naturaleza TEF", "fieldtype": "Select", "options": "\n06\n12", "read_only": 1, "insert_after": "clabe",
         "in_list_view": 1,
         "description": "06 = cuenta Banamex (sucursal + cuenta); 12 = interbancario por CLABE"},
        {"fieldname": "sucursal_banamex", "label": "Sucursal Banamex (4)", "fieldtype": "Data", "length": 4, "insert_after": "tipo_pago_tef", "depends_on": "eval:doc.tipo_pago_tef=='06'"},
        {"fieldname": "cuenta_banamex", "label": "Cuenta Banamex (7)", "fieldtype": "Data", "length": 7, "insert_after": "sucursal_banamex", "depends_on": "eval:doc.tipo_pago_tef=='06'"},
        {"fieldname": "col_tef", "fieldtype": "Column Break", "insert_after": "cuenta_banamex"},
        {"fieldname": "nombre_tef", "label": "Beneficiario en el archivo (55)", "fieldtype": "Data", "length": 55, "insert_after": "col_tef",
         "description": "Mayúsculas sin acentos. Física: NOMBRES,PATERNO/MATERNO · Moral: PRIMERA,RESTO DE LA RAZON SOCIAL/"},
        {"fieldname": "verificada", "label": "Verificada por Tesorería", "fieldtype": "Check", "read_only": 1, "insert_after": "nombre_tef", "in_list_view": 1},
        {"fieldname": "verificada_por", "label": "Verificada por", "fieldtype": "Link", "options": "User", "read_only": 1, "insert_after": "verificada"},
        {"fieldname": "verificada_el", "label": "Verificada el", "fieldtype": "Datetime", "read_only": 1, "insert_after": "verificada_por"},
        # Alta de la cuenta en BancaNet: Banamex no deja pagar a una cuenta que no está dada de alta
        # en el contrato. La sección siguiente en el meta estándar es `address_and_contact`, así que
        # esta no se traga ningún campo de ERPNext (lo vigila
        # test_produccion_pagos.test_las_secciones_nuevas_no_se_tragan_campos_estandar).
        {"fieldname": "sec_preregistro", "label": "Pre-registro en BancaNet", "fieldtype": "Section Break",
         "insert_after": "verificada_el", "depends_on": "eval:doc.party_type=='Supplier'"},
        {"fieldname": "estado_preregistro", "label": "Alta en el banco", "fieldtype": "Select",
         "options": "Sin registrar\nEnviada al banco\nRegistrada\nRechazada", "default": "Sin registrar",
         "read_only": 1, "in_list_view": 1, "in_standard_filter": 1, "insert_after": "sec_preregistro",
         "description": "Lo mueven la descarga del pre-registro y la carga de la respuesta del banco"},
        {"fieldname": "preregistro_enviado_el", "label": "Pre-registro enviado el", "fieldtype": "Datetime",
         "read_only": 1, "insert_after": "estado_preregistro"},
        {"fieldname": "col_preregistro", "fieldtype": "Column Break", "insert_after": "preregistro_enviado_el"},
        {"fieldname": "preregistro_respuesta", "label": "Respuesta del banco", "fieldtype": "Data", "length": 140,
         "read_only": 1, "insert_after": "col_preregistro"},
        # Tope que se le pide al banco para esta cuenta. Vacío = el de Configuración CxP (un default
        # estático no puede leer la configuración, así que la omisión se resuelve al armar la fila).
        {"fieldname": "importe_maximo_banco", "label": "Importe máximo autorizado en el banco", "fieldtype": "Currency",
         "insert_after": "preregistro_respuesta"},
    ],
    # El pago se crea al aplicar el resultado del banco: el lote, la autorización y la clave de
    # rastreo se escriben sobre el Payment Entry ya confirmado, de ahí el allow_on_submit.
    # `sec_lote` va después de `clearance_date` y no después de `reference_date` a propósito: un
    # Section Break se lleva consigo todos los campos que vengan después en el meta hasta el
    # siguiente corte, y en Payment Entry v15 el orden es reference_date → clearance_date →
    # accounting_dimensions_section. Colgada de reference_date, la sección (collapsible) se tragaba
    # `clearance_date`, que es el campo con el que ERPNext concilia el pago en el estado de cuenta.
    "Payment Entry": [
        {"fieldname": "sec_lote", "label": "Lote de pago Banamex", "fieldtype": "Section Break", "insert_after": "clearance_date", "collapsible": 1},
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
