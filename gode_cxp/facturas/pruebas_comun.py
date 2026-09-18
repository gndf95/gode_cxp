"""Prepara el sitio de pruebas (cuentas, artículo, configuración) y limpia lo que crean las pruebas."""
import frappe

from gode_cxp.cfdi import ejemplos
from gode_cxp.setup.produccion import CUENTAS, MODO_PAGO_TRANSFERENCIA, configurar_empresa

EMPRESA = "GODE PRUEBAS"
# Cuenta contable de banco que el plan de cuentas mexicano de GODE PRUEBAS NO trae (sólo trae el
# grupo "BANCOS E INTITUCIONES FINANCIERAS"). En producción la cuenta real ya existe en el catálogo.
CUENTA_BANCO_PRUEBAS = "Banco pruebas"
RFCS_PRUEBA = ("AVI900101AB1", "AVI900101AB2", "HESB850101AB1")
USUARIOS_PRUEBA = ("prueba.revisor@cxp.local", "prueba.tesoreria@cxp.local", "prueba.conta@cxp.local",
                   "prueba.sysadmin@cxp.local", "prueba.contable@cxp.local")


def preparar_sitio_pruebas():
    """Deja el sitio de pruebas listo con el MISMO script que se correrá en producción
    (setup/produccion.configurar_empresa), para no tener dos configuraciones que puedan divergir.
    Lo único que se agrega aparte es el modo de pruebas del correo."""
    if not frappe.db.exists("Company", EMPRESA):
        frappe.throw(f"No existe la empresa '{EMPRESA}': preparar_sitio_pruebas() solo corre en el sitio de pruebas.")
    # El catálogo de pagos va PRIMERO: `Configuracion CxP.modo_pago_transferencia` trae
    # 'Transferencia bancaria' como default, así que en un sitio nuevo el primer save del Single
    # (el que hace configurar_empresa) fallaría con un Link a un modo de pago que no existe.
    asegurar_catalogo_de_pagos()
    configurar_empresa(EMPRESA, ejemplos.RFC_EMPRESA, dry_run=False)
    if not frappe.db.get_single_value("Configuracion CxP", "modo_pruebas_correo"):
        frappe.db.set_single_value("Configuracion CxP", "modo_pruebas_correo", 1)
    conf = frappe.get_doc("Configuracion CxP")
    return {campo: conf.get(campo) for campo in CUENTAS}


def asegurar_catalogo_de_pagos():
    """Lo que `configurar_pagos` da por hecho del catálogo y este sitio de pruebas no trae: una
    cuenta contable de banco de detalle y el modo de pago de las transferencias. En producción las
    dos cosas existen de antes (la cuenta real del banco y el modo de pago del alta de ERPNext), así
    que esto es fixture de pruebas, no configuración de la app. Idempotente."""
    # Red de seguridad, como en limpiar(): crea catálogo, nunca debe correr en producción. Aquí no
    # sirve mirar Configuracion CxP.empresa (esto corre ANTES de que se llene), así que la guarda es
    # la existencia de la empresa de pruebas, igual que en preparar_sitio_pruebas().
    if not frappe.db.exists("Company", EMPRESA):
        frappe.throw(f"asegurar_catalogo_de_pagos() solo corre en el sitio de pruebas "
                     f"(no existe la empresa '{EMPRESA}').")
    hizo_falta = False
    if not frappe.db.exists("Mode of Payment", MODO_PAGO_TRANSFERENCIA):
        frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": MODO_PAGO_TRANSFERENCIA,
                        "type": "Bank"}).insert(ignore_permissions=True)
        hizo_falta = True
    if not frappe.db.get_value("Account", {"company": EMPRESA, "account_type": "Bank", "is_group": 0}, "name"):
        padre = frappe.db.get_value("Account", {"company": EMPRESA, "is_group": 1, "account_type": "Bank"},
                                    "name", order_by="lft")
        if not padre:
            frappe.throw(f"El catálogo de '{EMPRESA}' no tiene ningún grupo de cuentas de tipo Bank "
                         f"donde colgar '{CUENTA_BANCO_PRUEBAS}'.")
        frappe.get_doc({"doctype": "Account", "account_name": CUENTA_BANCO_PRUEBAS, "parent_account": padre,
                        "company": EMPRESA, "root_type": "Asset", "is_group": 0,
                        "account_type": "Bank"}).insert(ignore_permissions=True)
        hizo_falta = True
    if hizo_falta:
        frappe.db.commit()


def limpiar():
    # Red de seguridad: limpiar() borra CFDI y facturas en bloque, nunca debe tocar producción.
    if frappe.db.get_single_value("Configuracion CxP", "empresa") != EMPRESA:
        frappe.throw(f"limpiar() solo corre en el sitio de pruebas (Configuración CxP.empresa = {EMPRESA})")
    # Dos filtros para Purchase Invoice: la enmienda de una factura pierde el UUID (ver
    # facturas/eventos.py) pero conserva el enlace al CFDI, y también hay que borrarla.
    for dt, filtros in (("Purchase Invoice", {"cfdi_uuid": ["!=", ""]}),
                        ("Purchase Invoice", {"cfdi_recibido": ["!=", ""]}),
                        ("CFDI Recibido", {})):
        for name in frappe.get_all(dt, filters=filtros, pluck="name"):
            doc = frappe.get_doc(dt, name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc(dt, name, force=1, ignore_permissions=True, delete_permanently=True)
    # Las cuentas bancarias de los proveedores de prueba se borran ANTES que los proveedores: un
    # Bank Account que apunta al proveedor bloquea su borrado (Link en uso). Y hay que borrarlas
    # aunque el proveedor sobreviva: el name de Bank Account es account_name + " - " + banco, así
    # que la cuenta que dejó una prueba choca por nombre repetido con la que crea la siguiente.
    proveedores = frappe.get_all("Supplier", filters={"tax_id": ["in", RFCS_PRUEBA]}, pluck="name")
    if proveedores:
        for name in frappe.get_all("Bank Account",
                                   filters={"party_type": "Supplier", "party": ["in", proveedores]},
                                   pluck="name"):
            frappe.delete_doc("Bank Account", name, force=1, ignore_permissions=True, delete_permanently=True)
    for name in proveedores:
        frappe.delete_doc("Supplier", name, force=1, ignore_permissions=True)
    # Los usuarios de prueba no deben quedar vivos en el sitio; test_flujo los vuelve a crear.
    for correo in USUARIOS_PRUEBA:
        if frappe.db.exists("User", correo):
            frappe.delete_doc("User", correo, force=1, ignore_permissions=True, delete_permanently=True)
    # Las pruebas de la bandeja suben XML, ZIP y PDF sueltos (File sin adjuntar). Los que la carga
    # no borra (un archivo ajeno, uno que falló) se quedarían acumulándose en el sitio de pruebas.
    for name in frappe.get_all("File", filters={"attached_to_doctype": ["is", "not set"], "is_folder": 0},
                               or_filters=[["file_name", "like", "%.xml"], ["file_name", "like", "%.zip"], ["file_name", "like", "%.pdf"]],
                               pluck="name"):
        frappe.delete_doc("File", name, force=1, ignore_permissions=True, delete_permanently=True)
    frappe.db.commit()
