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
}
