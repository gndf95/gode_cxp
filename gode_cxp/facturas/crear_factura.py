"""Factura de compra en borrador a partir de un CFDI Recibido (ingreso o egreso)."""
import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate

from gode_cxp.facturas.proveedores import proveedor_por_rfc

TOLERANCIA = 0.01


def crear_factura_desde_cfdi(cfdi_name):
    cfdi = frappe.get_doc("CFDI Recibido", cfdi_name)
    if cfdi.factura:
        return cfdi.factura
    if cfdi.estado != "Nuevo":
        frappe.throw(_("El CFDI {0} está en estado {1}; solo se facturan los Nuevos.").format(cfdi.name, cfdi.estado))
    if cfdi.tipo_comprobante not in ("I", "E"):
        frappe.throw(_("El CFDI {0} es tipo {1}; solo ingresos (I) y egresos (E) generan factura.").format(cfdi.name, cfdi.tipo_comprobante))
    conf = frappe.get_single("Configuracion CxP")
    for campo in ("empresa", "item_generico", "cuenta_gasto_default", "cuenta_iva_acreditable"):
        if not conf.get(campo):
            frappe.throw(_("Falta '{0}' en Configuración CxP.").format(conf.meta.get_label(campo)))

    es_retorno = cfdi.tipo_comprobante == "E"
    signo = -1 if es_retorno else 1
    proveedor = proveedor_por_rfc(cfdi.rfc_emisor, cfdi.nombre_emisor, cfdi.moneda)
    fecha = getdate(cfdi.fecha_emision)
    pi = frappe.new_doc("Purchase Invoice")
    pi.update({
        "company": conf.empresa, "supplier": proveedor, "currency": cfdi.moneda, "conversion_rate": flt(cfdi.tipo_cambio) or 1,
        "set_posting_time": 1, "posting_date": fecha, "bill_no": _referencia(cfdi),
        "bill_date": fecha, "due_date": add_days(fecha, conf.dias_credito_default or 30),
        "cfdi_uuid": cfdi.uuid, "cfdi_recibido": cfdi.name, "rfc_emisor": cfdi.rfc_emisor,
        "metodo_pago_sat": cfdi.metodo_pago, "forma_pago_sat": cfdi.forma_pago, "estado_revision": "Recibida",
        "is_return": 1 if es_retorno else 0, "update_stock": 0,
    })
    if es_retorno:
        pi.return_against = _factura_relacionada(cfdi)
    cuenta_gasto = frappe.db.get_value("Supplier", proveedor, "default_expense_account") if frappe.get_meta("Supplier").has_field("default_expense_account") else None
    for c in cfdi.conceptos:
        cantidad = flt(c.cantidad) or 1
        neto = flt(c.importe) - flt(c.descuento)
        descripcion = c.descripcion or ""
        rate = round(neto / cantidad, 2)
        if abs(rate * cantidad - neto) > 0.005:
            # El precio unitario no cabe en 2 decimales: se factura 1 unidad por el importe neto
            # y el detalle real del CFDI se guarda en la descripción.
            qty = signo * 1
            rate = round(neto, 2)
            descripcion += " ({0:g} {1} × {2:.2f})".format(cantidad, c.unidad or c.clave_unidad, flt(c.valor_unitario))
        else:
            qty = signo * cantidad
        pi.append("items", {
            "item_code": conf.item_generico, "item_name": (c.descripcion or "Concepto")[:140], "description": descripcion,
            "qty": qty, "rate": rate, "expense_account": cuenta_gasto or conf.cuenta_gasto_default,
        })
    _agregar_impuesto(pi, "Add", conf.cuenta_iva_acreditable, "IVA acreditable", signo * flt(cfdi.iva_trasladado))
    _agregar_impuesto(pi, "Add", conf.cuenta_ieps, "IEPS", signo * flt(cfdi.ieps))
    _agregar_impuesto(pi, "Deduct", conf.cuenta_ret_iva, "IVA retenido", signo * flt(cfdi.iva_retenido))
    _agregar_impuesto(pi, "Deduct", conf.cuenta_ret_isr, "ISR retenido", signo * flt(cfdi.isr_retenido))
    pi.insert(ignore_permissions=True)

    diferencia = abs(flt(pi.grand_total) - signo * flt(cfdi.total))
    if diferencia > TOLERANCIA:
        # db_set y no save(): con el workflow activo, un guardado que cambia el estado sin una
        # transición se rechaza. "Error de lectura" lo marca la app, no una acción de usuario.
        pi.db_set({
            "estado_revision": "Error de lectura",
            "nota_aclaracion": _("El total del XML ({0}) no coincide con el total de la factura ({1}). Revisar impuestos y conceptos antes de aprobar.").format(cfdi.total, pi.grand_total),
        })

    cfdi.db_set({"factura": pi.name, "estado": "Con factura", "proveedor": proveedor})
    return pi.name


def _agregar_impuesto(pi, tipo, cuenta, descripcion, monto):
    if not monto:
        return
    if not cuenta:
        frappe.throw(_("El CFDI trae {0} pero no hay cuenta configurada para ello en Configuración CxP.").format(descripcion))
    # El signo ya viene del llamador (negativo en egresos): ERPNext espera montos negativos
    # en una devolución tanto en las filas Add como en las Deduct.
    pi.append("taxes", {"charge_type": "Actual", "add_deduct_tax": tipo, "category": "Total", "account_head": cuenta,
                        "description": descripcion, "tax_amount": monto})


def _referencia(cfdi):
    """Folio del proveedor: "serie-folio" si vienen los dos, si no lo que haya, y si no el UUID corto."""
    serie = (cfdi.serie or "").strip()
    folio = (cfdi.folio or "").strip()
    if serie and folio:
        return f"{serie}-{folio}"
    return serie or folio or cfdi.uuid[:8]


def _factura_relacionada(cfdi):
    """Para una nota de crédito: la factura enviada cuyo UUID aparece en CfdiRelacionados (si hay)."""
    datos = _relacionados(cfdi)
    for uuid in datos:
        name = frappe.db.get_value("Purchase Invoice", {"cfdi_uuid": uuid, "docstatus": 1, "is_return": 0}, "name")
        if name:
            return name
    return None


def _relacionados(cfdi):
    """UUIDs relacionados leídos otra vez del XML adjunto (el DocType no los guarda)."""
    from gode_cxp.cfdi.lector import leer_cfdi
    if not cfdi.archivo_xml:
        return []
    archivo = frappe.db.get_value("File", {"file_url": cfdi.archivo_xml}, "name")
    if not archivo:
        return []
    contenido = frappe.get_doc("File", archivo).get_content()
    if isinstance(contenido, str):
        contenido = contenido.encode("utf-8")
    return [r["uuid"] for r in leer_cfdi(contenido)["cfdi_relacionados"]]
