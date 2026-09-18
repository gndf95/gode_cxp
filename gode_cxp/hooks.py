app_name = "gode_cxp"
app_title = "Cuentas por Pagar"
app_publisher = "Grupo Garena"
app_description = "Cuentas por pagar de GODE: CFDI, lotes de pago Banamex, conciliación"
app_email = "sergio@urenque.com"
app_license = "mit"
required_apps = ["frappe/erpnext"]

# Cada migración deja la configuración (campos, roles, flujo, workspace) como debe estar.
after_migrate = ["gode_cxp.setup.instalar.asegurar_configuracion"]
after_install = ["gode_cxp.setup.instalar.asegurar_configuracion"]
