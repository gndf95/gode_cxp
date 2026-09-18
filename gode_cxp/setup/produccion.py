"""Configuración contable y de catálogos de una empresa para cuentas por pagar.

`configurar_empresa()` es idempotente y trae dry-run:

  - con `dry_run=True` (el valor por omisión) NO escribe nada -- ni siquiera hace commit -- y
    devuelve la lista de frases de lo que haría, para que una persona la lea antes de aplicarla;
  - con `dry_run=False` aplica sólo lo que falta; correrlo dos veces seguidas deja `acciones` vacía.

No está expuesta al web (no lleva @frappe.whitelist). Ejecutar con
`bench --site X execute gode_cxp.setup.produccion.configurar_empresa --kwargs ...`
(no desde `bench console` sin commit; nunca desde el navegador).
"""
import re

import frappe
from frappe import _

from gode_cxp.setup.instalar import GRUPO_PROVEEDORES, asegurar_grupo_proveedores

ITEM_GENERICO = "CFDI-CONCEPTO"
DIAS_CREDITO_DEFAULT = 30

# La cuenta por pagar por defecto no vive en Configuracion CxP sino en la Company (ERPNext la usa
# como `credit_to` de cada factura de compra), pero se valida con la misma receta que las demás.
CAMPO_POR_PAGAR = "default_payable_account"
CUENTA_POR_PAGAR = {
    "nombre": None,
    "etiqueta": "cuenta por pagar por defecto de la empresa",
    "root_type": "Liability",
    "account_type": "Payable",
    "alternas": (),
    "padres": (),
}

# Las cinco cuentas que la app necesita en Configuracion CxP. De cada una:
#   nombre       cómo se llamará la cuenta si hay que crearla (ERPNext le pega "- <abreviatura>")
#   etiqueta     cómo se le dice en el reporte, para que se lea en español
#   root_type    Asset / Liability / Expense; también acota la búsqueda de cuentas ya existentes
#   account_type tipo de ERPNext ("Tax" en las de impuestos), o None
#   alternas     nombres que quizá YA existan en el catálogo y hay que preferir antes de crear otra
#   padres       grupos donde colgarla, en orden de preferencia
CUENTAS = {
    "cuenta_gasto_default": {
        "nombre": "Compras y gastos CFDI",
        "etiqueta": "cuenta de gasto por defecto",
        "root_type": "Expense",
        "account_type": None,
        "alternas": (),
        "padres": ("GASTOS GENERALES", "GASTOS OPERATIVOS", "Gastos generales", "Indirect Expenses"),
    },
    "cuenta_iva_acreditable": {
        "nombre": "IVA acreditable",
        "etiqueta": "cuenta de IVA acreditable",
        "root_type": "Asset",
        "account_type": "Tax",
        "alternas": ("IVA ACREDITABLE", "IVA"),
        "padres": ("IMPUESTOS", "IMPUESTOS PAGADOS POR ANTICIPADOS", "Tax Assets"),
    },
    "cuenta_ieps": {
        "nombre": "IEPS acreditable",
        "etiqueta": "cuenta de IEPS acreditable",
        "root_type": "Asset",
        "account_type": "Tax",
        "alternas": ("IEPS ACREDITABLE", "IEPS"),
        "padres": ("IMPUESTOS", "IMPUESTOS PAGADOS POR ANTICIPADOS", "Tax Assets"),
    },
    "cuenta_ret_iva": {
        "nombre": "IVA retenido por pagar",
        "etiqueta": "cuenta de IVA retenido",
        "root_type": "Liability",
        "account_type": "Tax",
        "alternas": ("IVA RETENIDO",),
        "padres": ("RETENCIONES E IMPUESTOS POR PAGAR", "IMPUESTOS POR PAGAR", "Duties and Taxes"),
    },
    "cuenta_ret_isr": {
        "nombre": "ISR retenido por pagar",
        "etiqueta": "cuenta de ISR retenido",
        "root_type": "Liability",
        "account_type": "Tax",
        "alternas": ("ISR RETENIDO",),
        "padres": ("RETENCIONES E IMPUESTOS POR PAGAR", "IMPUESTOS POR PAGAR", "Duties and Taxes"),
    },
}

# Cómo se nombran en el reporte los campos de Configuracion CxP que no son cuentas.
ETIQUETAS = {
    "empresa": "empresa",
    "rfc_empresa": "RFC de la empresa",
    "grupo_proveedores": "grupo de proveedores",
    "item_generico": "artículo genérico de los conceptos",
    "dias_credito_default": "días de crédito por defecto",
}

NADA_QUE_HACER = "Nada que hacer: la empresa ya está configurada."

# RFC de persona moral (12) o física (13): 3-4 letras (o Ñ/&), 6 dígitos de fecha, 3 caracteres de
# homoclave. No valida el dígito verificador ni que exista en el SAT, sólo la forma.
RFC_REGEX = re.compile(r"^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$")


def configurar_empresa(company, rfc, dry_run=True, cuentas=None):
    """Deja la empresa lista para cuentas por pagar y devuelve el reporte de lo que hizo (o haría).

    `cuentas` es opcional: {campo: nombre de una cuenta que ya existe}, para cuando en producción
    conviene apuntar a una cuenta del catálogo en vez de crear una nueva. Las claves válidas son
    los cinco campos de Configuracion CxP y `default_payable_account` (el de la Company).

    Devuelve {"dry_run", "empresa", "acciones", "resumen"}: `acciones` trae sólo las cosas por
    hacer (queda vacía si no hay ninguna) y `resumen` es la frase que resume la corrida.
    """
    if not frappe.db.exists("Company", company):
        frappe.throw(_("No existe la empresa '{0}'.").format(company))
    cuentas = _validar_las_cuentas_a_mano(company, cuentas)
    rfc_normalizado = _validar_rfc(rfc)
    acciones = []
    empresa = frappe.get_doc("Company", company)
    _cuenta_por_pagar(empresa, cuentas.get(CAMPO_POR_PAGAR), acciones, dry_run)

    conf = frappe.get_doc("Configuracion CxP")
    valores = {
        "empresa": company,
        "rfc_empresa": rfc_normalizado,
        "grupo_proveedores": GRUPO_PROVEEDORES,
        "item_generico": ITEM_GENERICO,
        "dias_credito_default": conf.dias_credito_default or DIAS_CREDITO_DEFAULT,
    }
    for campo in CUENTAS:
        valores[campo] = _resolver_cuenta(company, empresa.abbr, campo, conf,
                                          cuentas.get(campo), acciones, dry_run)

    _asegurar_item(acciones, dry_run)
    _asegurar_grupo(acciones, dry_run)

    cambios = {campo: valor for campo, valor in valores.items() if (conf.get(campo) or None) != (valor or None)}
    for campo, valor in cambios.items():
        etiqueta = ETIQUETAS.get(campo) or CUENTAS[campo]["etiqueta"]
        antes = conf.get(campo) or "vacío"
        acciones.append(f"Configuración CxP: poner '{valor}' como {etiqueta} (ahora dice '{antes}')")
    if cambios and not dry_run:
        conf.update(cambios)
        conf.save(ignore_permissions=True)

    if not dry_run and acciones:
        frappe.db.commit()
    return {"dry_run": dry_run, "empresa": company, "acciones": acciones,
            "resumen": _resumen(acciones, dry_run)}


def _validar_rfc(rfc):
    """Normaliza y valida la forma del RFC antes de escribir nada: sólo la forma (12 o 13
    caracteres, con el patrón de letras/fecha/homoclave), no el dígito verificador ni que exista
    en el SAT."""
    normalizado = (rfc or "").strip().upper()
    if len(normalizado) not in (12, 13) or not RFC_REGEX.match(normalizado):
        frappe.throw(_("El RFC '{0}' no tiene una forma válida (12 o 13 caracteres: letras, fecha "
                       "y homoclave).").format(rfc))
    return normalizado


def _resumen(acciones, dry_run):
    """La frase de una línea que resume la corrida. Va aparte de `acciones` a propósito: `acciones`
    es la lista de cosas por hacer y tiene que quedar vacía cuando no hay ninguna."""
    if not acciones:
        return NADA_QUE_HACER
    if dry_run:
        return f"Dry-run: {len(acciones)} cosas por hacer; no se escribió nada."
    return f"Aplicado: {len(acciones)} cosas."


def _validar_las_cuentas_a_mano(company, cuentas):
    """Revisa el parámetro `cuentas` antes de tocar nada: una clave mal escrita o una cuenta ajena
    tiene que fallar al principio, no a media configuración."""
    campos_validos = [CAMPO_POR_PAGAR] + list(CUENTAS)
    desconocidas = [campo for campo in (cuentas or {}) if campo not in campos_validos]
    if desconocidas:
        frappe.throw(_("No sé qué hacer con estas claves de 'cuentas': {0}. Las válidas son: {1}.")
                     .format(", ".join(sorted(desconocidas)), ", ".join(campos_validos)))
    cuentas = {campo: cuenta for campo, cuenta in (cuentas or {}).items() if cuenta}
    for campo, cuenta in cuentas.items():
        receta = CUENTA_POR_PAGAR if campo == CAMPO_POR_PAGAR else CUENTAS[campo]
        datos = frappe.db.get_value("Account", cuenta, ["company", "is_group", "account_currency"], as_dict=True)
        if not datos:
            frappe.throw(_("No existe la cuenta '{0}' que se pidió usar como {1}.")
                         .format(cuenta, receta["etiqueta"]))
        if datos.company != company:
            frappe.throw(_("La cuenta '{0}' es de la empresa '{1}', no de '{2}'; no sirve como {3}.")
                         .format(cuenta, datos.company, company, receta["etiqueta"]))
        if datos.is_group:
            frappe.throw(_("La cuenta '{0}' es un grupo; para {1} hace falta una cuenta de detalle.")
                         .format(cuenta, receta["etiqueta"]))
        # La cuenta por pagar por defecto es la que ERPNext usa como 'credit_to' de cada factura de
        # compra: en otra moneda que la de la empresa rompería esas facturas. A diferencia del
        # root_type y el account_type (que sólo se avisan con REVISAR), esto se rechaza.
        if campo == CAMPO_POR_PAGAR and datos.account_currency:
            moneda_empresa = frappe.db.get_value("Company", company, "default_currency")
            if datos.account_currency != moneda_empresa:
                frappe.throw(_("La cuenta '{0}' está en {1}, pero la empresa '{2}' usa {3}: no sirve "
                               "como {4}.").format(cuenta, datos.account_currency, company,
                                                   moneda_empresa, receta["etiqueta"]))
    return cuentas


def _cuenta_por_pagar(empresa, a_mano, acciones, dry_run):
    """La cuenta por pagar por defecto de la empresa: ERPNext la usa como 'credit_to' de cada
    factura de compra. Si la empresa ya apunta a una cuenta que sirve, NO se toca: en producción
    esa cuenta está puesta a propósito y moverla cambiaría dónde caen los asientos."""
    actual = empresa.default_payable_account
    if a_mano:
        # Elegida a mano: los avisos salen siempre, y se pone aunque ya hubiera una válida.
        _avisar_si_no_es_del_root(a_mano, CUENTA_POR_PAGAR, acciones)
        _avisar_si_no_es_del_tipo(a_mano, CUENTA_POR_PAGAR, acciones)
        nueva = a_mano
    elif _sirve_como_cuenta_por_pagar(empresa, actual):
        return
    else:
        nueva = _elegir_cuenta_por_pagar(empresa)
    if actual == nueva:
        return
    acciones.append(f"Cambiar la cuenta por pagar por defecto de la empresa de "
                    f"'{actual or 'vacía'}' a '{nueva}'")
    if not dry_run:
        empresa.default_payable_account = nueva
        empresa.save(ignore_permissions=True)


def _sirve_como_cuenta_por_pagar(empresa, cuenta):
    """¿La cuenta que ya trae la empresa sirve? Tiene que ser de la empresa, de tipo Payable, de
    detalle y en la moneda de la empresa (una cuenta en otra moneda rompería las facturas)."""
    if not cuenta:
        return False
    datos = frappe.db.get_value("Account", cuenta,
                                ["company", "account_type", "is_group", "account_currency"], as_dict=True)
    if not datos:
        return False
    return bool(datos.company == empresa.name
                and datos.account_type == "Payable"
                and not datos.is_group
                and (not datos.account_currency or datos.account_currency == empresa.default_currency))


def _elegir_cuenta_por_pagar(empresa):
    """La candidata cuando la empresa no tiene una que sirva: se prefiere la que hable de
    proveedores; si no hay, la primera del catálogo (por `lft`, para que sea determinista)."""
    candidatas = frappe.get_all("Account",
                                filters={"company": empresa.name, "account_type": "Payable", "is_group": 0},
                                fields=["name", "account_name", "account_currency"], order_by="lft")
    en_moneda = [c for c in candidatas
                 if not c.account_currency or c.account_currency == empresa.default_currency]
    if not en_moneda:
        frappe.throw(_("La empresa '{0}' no tiene ninguna cuenta por pagar (account_type = Payable) en {1}.")
                     .format(empresa.name, empresa.default_currency))
    proveedores = [c for c in en_moneda if "PROVEEDOR" in (c.account_name or "").upper()]
    return (proveedores or en_moneda)[0].name


def _resolver_cuenta(company, abbr, campo, conf, a_mano, acciones, dry_run):
    """Devuelve el nombre de la cuenta que debe quedar en `campo`, creándola si hace falta."""
    receta = CUENTAS[campo]
    if a_mano:
        # Elegida a mano: los avisos salen siempre, también en dry-run y aunque no cambie nada,
        # porque hablan de la cuenta que va a quedar configurada, no del cambio.
        _avisar_si_no_es_del_root(a_mano, receta, acciones)
        _avisar_si_no_es_del_tipo(a_mano, receta, acciones)
        return a_mano
    actual = conf.get(campo)
    if actual and frappe.db.get_value("Account", actual, "company") == company:
        return actual        # ya está bien puesta: ni se busca ni se toca
    existente = _buscar_cuenta(company, receta)
    if existente:
        acciones.append(f"Reusar la cuenta '{existente}' del catálogo como {receta['etiqueta']}")
        _avisar_si_no_es_del_tipo(existente, receta, acciones)
        return existente
    padre = _cuenta_padre(company, receta)
    if not padre:
        frappe.throw(_("No hay ningún grupo de cuentas de tipo {0} en '{1}' donde colgar '{2}'.")
                     .format(receta["root_type"], company, receta["nombre"]))
    nombre_completo = f"{receta['nombre']} - {abbr}"
    if frappe.db.exists("Account", nombre_completo):
        # Existe pero _buscar_cuenta no la encontró (es grupo o de otro root_type/account_type):
        # no se pisa ni se intenta crear un duplicado, que Frappe rechazaría de todos modos.
        frappe.throw(_("La cuenta {0} ya existe pero no sirve como {1} (es grupo o de otro tipo). "
                       "Pásala o elige otra con cuentas={{...}}").format(nombre_completo, receta["etiqueta"]))
    acciones.append(f"Crear la cuenta '{nombre_completo}' bajo '{padre}' para usarla como {receta['etiqueta']}")
    if dry_run:
        return nombre_completo
    doc = frappe.get_doc({"doctype": "Account", "account_name": receta["nombre"], "parent_account": padre,
                          "company": company, "root_type": receta["root_type"], "is_group": 0,
                          "account_type": receta["account_type"]}).insert(ignore_permissions=True)
    return doc.name


def _avisar_si_no_es_del_tipo(cuenta, receta, acciones):
    """Si se reaprovecha una cuenta del catálogo que no está marcada como el tipo que ERPNext espera
    (las de impuestos deberían ser 'Tax'), se dice en el reporte en vez de cambiársela por la fuerza."""
    if not receta["account_type"]:
        return
    tipo = frappe.db.get_value("Account", cuenta, "account_type")
    if tipo != receta["account_type"]:
        acciones.append(f"REVISAR: se va a usar '{cuenta}' como {receta['etiqueta']}, pero en ERPNext "
                        f"es de tipo '{tipo or 'ninguno'}' y debería ser '{receta['account_type']}'")


def _avisar_si_no_es_del_root(cuenta, receta, acciones):
    """Lo mismo con el root_type. No se rechaza (el catálogo es de la empresa, no nuestro), pero una
    cuenta de gasto puesta donde va un activo tiene que saltar a la vista en el reporte."""
    root = frappe.db.get_value("Account", cuenta, "root_type")
    if root != receta["root_type"]:
        acciones.append(f"REVISAR: se va a usar '{cuenta}' como {receta['etiqueta']}, pero es de "
                        f"'{root or 'ninguno'}' y esta app la espera de '{receta['root_type']}'")


def _buscar_cuenta(company, receta):
    """Una cuenta del catálogo que ya sirva para esto. Se busca primero por el nombre que pone este
    script (para que correrlo dos veces no duplique nada) y luego por los nombres alternos. Siempre
    dentro del mismo root_type: hay catálogos con un 'IVA' de pasivo que no es el acreditable."""
    for nombre in (receta["nombre"], *receta["alternas"]):
        cuenta = frappe.db.get_value("Account", {"company": company, "account_name": nombre, "is_group": 0,
                                                 "root_type": receta["root_type"]}, "name", order_by="lft")
        if cuenta:
            return cuenta
    return None


def _cuenta_padre(company, receta):
    """El grupo donde colgar la cuenta nueva: primero los nombres preferidos, luego un grupo del
    mismo account_type y, si no hay, el primer grupo del root_type que no sea la raíz."""
    filtros = {"company": company, "root_type": receta["root_type"], "is_group": 1, "parent_account": ["is", "set"]}
    for nombre in receta["padres"]:
        padre = frappe.db.get_value("Account", dict(filtros, account_name=nombre), "name", order_by="lft")
        if padre:
            return padre
    if receta["account_type"]:
        padre = frappe.db.get_value("Account", dict(filtros, account_type=receta["account_type"]), "name", order_by="lft")
        if padre:
            return padre
    return frappe.db.get_value("Account", filtros, "name", order_by="lft")


def _asegurar_item(acciones, dry_run):
    """El artículo con el que entra cada concepto del CFDI a la factura de compra."""
    if frappe.db.exists("Item", ITEM_GENERICO):
        # Ya existe: lo único que puede faltarle es que su unidad acepte cantidades con fracción.
        _permitir_decimales(frappe.db.get_value("Item", ITEM_GENERICO, "stock_uom"), acciones, dry_run)
        return
    acciones.append(f"Crear el artículo '{ITEM_GENERICO}' (con él entra cada concepto del CFDI a la factura)")
    uom = _elegir_uom()
    _permitir_decimales(uom, acciones, dry_run)
    if dry_run:
        return
    grupo = frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="lft")
    doc = frappe.get_doc({"doctype": "Item", "item_code": ITEM_GENERICO, "item_name": "Concepto CFDI",
                          "item_group": grupo, "stock_uom": uom, "is_stock_item": 0,
                          "is_purchase_item": 1, "is_sales_item": 0}).insert(ignore_permissions=True)
    # Si el sitio tiene Stock Settings > "Item Naming By" = "Naming Series", ERPNext ignora el
    # item_code que le pasamos y nombra el artículo con un folio de serie; entonces NO se llamaría
    # CFDI-CONCEPTO y la Configuración CxP apuntaría a un artículo inexistente. Se comprueba.
    if doc.name != ITEM_GENERICO:
        frappe.throw(_("El artículo quedó como '{0}' y no como '{1}': este sitio nombra los artículos "
                       "por serie. Cambia Stock Settings > 'Item Naming By' a 'Item Code' y vuelve a "
                       "correr esto (al fallar no se guarda nada).").format(doc.name, ITEM_GENERICO))


def _elegir_uom():
    """La unidad del artículo genérico: da igual cuál sea (los conceptos llevan su unidad real en la
    descripción), pero tiene que existir en el sitio."""
    return (frappe.db.get_value("UOM", {"name": ["in", ["Nos", "Nos.", "Unit", "Unidad(es)"]]}, "name")
            or frappe.db.get_value("UOM", {}, "name", order_by="name"))


def _permitir_decimales(uom, acciones, dry_run):
    """Un CFDI puede traer cantidades con fracción (10.26 kg de pollo). Si la unidad del artículo
    está marcada como 'debe ser número entero' -- 'Nos' viene así de fábrica en ERPNext --, la
    factura se rechaza con "la cantidad no puede ser una fracción" y el CFDI no se puede capturar.
    Se le quita la marca a esa unidad; es un ajuste del catálogo, no de la factura."""
    if not uom or not frappe.db.get_value("UOM", uom, "must_be_whole_number"):
        return
    acciones.append(f"Permitir decimales en la unidad '{uom}' (los CFDI traen cantidades con fracción)")
    if not dry_run:
        # db.set_value y no el doc: la UOM no tiene validaciones y así no se toca nada más de ella.
        # El valor que lee ERPNext (frappe.db.get_values con cache) vive en la conexión y se limpia
        # en cada commit, así que el cambio se ve en cuanto se confirma.
        frappe.db.set_value("UOM", uom, "must_be_whole_number", 0)


def _asegurar_grupo(acciones, dry_run):
    if frappe.db.exists("Supplier Group", GRUPO_PROVEEDORES):
        return
    acciones.append(f"Crear el grupo de proveedores '{GRUPO_PROVEEDORES}' (ahí se dan de alta los que llegan por CFDI)")
    if not dry_run:
        asegurar_grupo_proveedores()
