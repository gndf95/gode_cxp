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


def usuario(correo, rol):
    """Usuario de prueba con un solo rol CxP. limpiar() los borra (USUARIOS_PRUEBA), así que las
    pruebas los recrean en cada setUp. Vive aquí y no en un test_*.py para que las pruebas de un
    módulo no tengan que importar las de otro."""
    if not frappe.db.exists("User", correo):
        u = frappe.get_doc({"doctype": "User", "email": correo, "first_name": correo.split("@")[0],
                            "send_welcome_email": 0})
        u.append("roles", {"role": rol})
        u.insert(ignore_permissions=True)
    return correo


def factura_aprobada(xml_bytes, nombre="factura.xml"):
    """Procesa el XML, crea la factura y la lleva hasta Aprobada (docstatus 1) como Administrator.

    Las pruebas de pagos necesitan facturas ya aprobadas, no el flujo de revisión en sí (eso lo
    prueba facturas/test_flujo.py), así que aquí se recorre el flujo con un solo usuario.
    'Administrator' tiene todos los roles del sitio, así que pasa por todas las transiciones."""
    from frappe.model.workflow import apply_workflow

    from gode_cxp.facturas.crear_factura import crear_factura_desde_cfdi
    from gode_cxp.facturas.recepcion import procesar_xml
    usuario_antes = frappe.session.user
    frappe.set_user("Administrator")
    try:
        cfdi = procesar_xml(xml_bytes, "Carga manual", nombre)
        pi = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(cfdi.name))
        pi = apply_workflow(pi, "Enviar a revisión")
        pi.recepcion_confirmada = 1
        pi.save()
        pi = apply_workflow(pi, "Confirmar recepción")
        pi = apply_workflow(pi, "Aprobar")
        return frappe.get_doc("Purchase Invoice", pi.name)
    finally:
        frappe.set_user(usuario_antes)


def cuenta_verificada(proveedor, clabe, banco="Banorte", sucursal=None, cuenta=None):
    """Bank Account de proveedor con CLABE y ya verificada por Tesorería."""
    from gode_cxp.pagos.cuentas_bancarias import verificar_cuenta
    if not frappe.db.exists("Bank", banco):
        frappe.get_doc({"doctype": "Bank", "bank_name": banco}).insert(ignore_permissions=True)
    _nombre_de_persona_fisica(proveedor)
    doc = frappe.get_doc({"doctype": "Bank Account", "account_name": f"Cuenta {proveedor}"[:140], "bank": banco,
                          "party_type": "Supplier", "party": proveedor, "clabe": clabe,
                          "sucursal_banamex": sucursal, "cuenta_banamex": cuenta, "is_default": 1})
    doc.insert(ignore_permissions=True)
    verificar_cuenta(doc.name)
    return doc.name


def _nombre_de_persona_fisica(proveedor):
    """Una cuenta de proveedor persona física exige nombre y apellidos en el Supplier (el
    beneficiario del TEF va NOMBRES,PATERNO/MATERNO y no se saca de la razón social). Los
    proveedores que nacen de un CFDI sólo traen el nombre del emisor, así que aquí se parte en tres:
    las dos últimas palabras son los apellidos. Es una heurística de pruebas, no de producción."""
    p = frappe.db.get_value("Supplier", proveedor,
                            ["tipo_persona", "nombre_pila", "apellido_paterno", "supplier_name"], as_dict=True)
    if p.tipo_persona != "Física" or p.nombre_pila or p.apellido_paterno:
        return
    palabras = (p.supplier_name or "").split()
    if len(palabras) < 3:
        frappe.throw(f"El proveedor de prueba '{proveedor}' es persona física y su nombre no se puede "
                     f"partir en nombre(s) + dos apellidos: captúralos en la prueba.")
    frappe.db.set_value("Supplier", proveedor, {"nombre_pila": " ".join(palabras[:-2]),
                                                "apellido_paterno": palabras[-2], "apellido_materno": palabras[-1]})


def xml_con(xml_bytes, uuid, folio):
    """Copia de un XML de ejemplo con otro UUID y folio (misma técnica que test_api)."""
    import re
    xml = re.sub(rb'UUID="[^"]+"', b'UUID="' + uuid.encode() + b'"', xml_bytes)
    return re.sub(rb'Folio="[^"]+"', b'Folio="' + folio.encode() + b'"', xml)


def _borrar(dt, name):
    doc = frappe.get_doc(dt, name)
    if doc.docstatus == 1:
        doc.cancel()
    frappe.delete_doc(dt, name, force=1, ignore_permissions=True, delete_permanently=True)


def limpiar():
    # Red de seguridad: limpiar() borra CFDI y facturas en bloque, nunca debe tocar producción.
    if frappe.db.get_single_value("Configuracion CxP", "empresa") != EMPRESA:
        frappe.throw(f"limpiar() solo corre en el sitio de pruebas (Configuración CxP.empresa = {EMPRESA})")
    proveedores = frappe.get_all("Supplier", filters={"tax_id": ["in", RFCS_PRUEBA]}, pluck="name")
    # --- lo que cuelga de las facturas se borra ANTES que ellas -----------------------------------
    # Al cancelar un Payment Entry, ERPNext le devuelve el saldo a la factura; al cancelar un Lote de
    # Pago, el hook le quita el `en_lote`. Si las facturas se fueran primero, las dos cosas fallarían.
    pagos = set(frappe.get_all("Payment Entry", filters={"lote_pago": ["is", "set"]}, pluck="name"))
    if proveedores:
        pagos |= set(frappe.get_all("Payment Entry", filters={"party_type": "Supplier", "party": ["in", proveedores]},
                                    pluck="name"))
    for name in pagos:
        _borrar("Payment Entry", name)
    # Resultado Bancario (llega en la Task 6): sólo existe en pruebas, se borran todos.
    if frappe.db.exists("DocType", "Resultado Bancario"):
        for name in frappe.get_all("Resultado Bancario", pluck="name"):
            _borrar("Resultado Bancario", name)
    for name in frappe.get_all("Lote de Pago", pluck="name"):
        # Un lote Transmitido o Aplicado no se deja cancelar (pagos/eventos.antes_de_cancelar), y sin
        # cancelarlo no se borra. En pruebas se le baja el estado para poder tirarlo.
        if frappe.db.get_value("Lote de Pago", name, "docstatus") == 1:
            frappe.db.set_value("Lote de Pago", name, "estado_lote", "Autorizado", update_modified=False)
        _borrar("Lote de Pago", name)
    # Las cuentas bancarias de los proveedores de prueba se borran ANTES que los proveedores: un
    # Bank Account que apunta al proveedor bloquea su borrado (Link en uso). Y hay que borrarlas
    # aunque el proveedor sobreviva: el name de Bank Account es account_name + " - " + banco, así
    # que la cuenta que dejó una prueba choca por nombre repetido con la que crea la siguiente.
    if proveedores:
        for name in frappe.get_all("Bank Account",
                                   filters={"party_type": "Supplier", "party": ["in", proveedores]},
                                   pluck="name"):
            frappe.delete_doc("Bank Account", name, force=1, ignore_permissions=True, delete_permanently=True)
    # Tres filtros para Purchase Invoice: la enmienda de una factura pierde el UUID (ver
    # facturas/eventos.py) pero conserva el enlace al CFDI; y las pruebas también crean facturas a
    # mano (copias sin CFDI) que sólo se reconocen por el proveedor de prueba.
    for dt, filtros in (("Purchase Invoice", {"cfdi_uuid": ["!=", ""]}),
                        ("Purchase Invoice", {"cfdi_recibido": ["!=", ""]}),
                        ("Purchase Invoice", {"supplier": ["in", proveedores or [""]]}),
                        ("CFDI Recibido", {})):
        for name in frappe.get_all(dt, filters=filtros, pluck="name"):
            _borrar(dt, name)
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
