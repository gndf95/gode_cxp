app_name = "gode_cxp"
app_title = "Cuentas por Pagar"
app_publisher = "Grupo Garena"
app_description = "Cuentas por pagar de GODE: CFDI, lotes de pago Banamex, conciliación"
app_email = "sergio@urenque.com"
app_license = "mit"
required_apps = ["frappe/erpnext"]

# Los roles deben existir ANTES de que Frappe sincronice los DocTypes que los usan en "permissions".
before_migrate = ["gode_cxp.setup.instalar.asegurar_roles"]

# Cada migración deja la configuración (campos, roles, flujo, workspace) como debe estar.
after_migrate = ["gode_cxp.setup.instalar.asegurar_configuracion"]
after_install = ["gode_cxp.setup.instalar.asegurar_configuracion"]

doc_events = {
    "Purchase Invoice": {
        "validate": "gode_cxp.facturas.eventos.validar_factura",
        "after_insert": "gode_cxp.facturas.eventos.apuntar_cfdi_a_la_enmienda",
        "before_submit": "gode_cxp.facturas.eventos.antes_de_enviar",
    },
    # Quien pueda escribir facturas necesita CxP Editor o el flujo le deja el formulario en solo
    # lectura; se sella al guardar el usuario, no sólo al migrar.
    "User": {"validate": "gode_cxp.setup.roles.sellar_rol_editor"},
    # Las cuentas bancarias de proveedor son las que alimentan el archivo TEF: la CLABE, la
    # naturaleza del pago y el nombre del beneficiario se validan aquí, no en el formulario.
    "Bank Account": {"validate": "gode_cxp.pagos.cuentas_bancarias.validar_cuenta_bancaria"},
    # El lote de pago aparta las facturas al autorizarse (submit) y las libera al cancelarse. Las
    # reglas van en doc_events para que también corran si alguien guarda el lote desde el formulario.
    "Lote de Pago": {
        "validate": "gode_cxp.pagos.eventos.validar_lote",
        # Guardar un lote ya enviado NO dispara `validate` (Frappe sólo corre
        # `before_update_after_submit`), y ahí es donde va la guardia de los campos de dinero.
        "before_update_after_submit": "gode_cxp.pagos.eventos.validar_cambios_del_lote_enviado",
        "on_submit": "gode_cxp.pagos.eventos.al_autorizar",
        "before_cancel": "gode_cxp.pagos.eventos.antes_de_cancelar",
        "on_cancel": "gode_cxp.pagos.eventos.al_cancelar",
    },
}

doctype_js = {"Bank Account": "public/js/bank_account.js"}
# El formulario del Lote de Pago no necesita hook (lote_de_pago.js vive junto a su DocType); la lista
# de facturas sí, porque Purchase Invoice es un DocType de ERPNext.
doctype_list_js = {"Purchase Invoice": "public/js/purchase_invoice_list.js"}
