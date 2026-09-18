"""Lectura de CFDI 3.3 y 4.0 con lxml. Módulo puro: no importa frappe."""
from datetime import datetime

from lxml import etree

from gode_cxp.cfdi.errores import CfdiInvalido

NS = {
    "cfdi3": "http://www.sat.gob.mx/cfd/3",
    "cfdi4": "http://www.sat.gob.mx/cfd/4",
    "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
}
IMPUESTO_ISR, IMPUESTO_IVA, IMPUESTO_IEPS = "001", "002", "003"


def _num(valor, default=0.0):
    return float(valor) if valor not in (None, "") else default


def _fecha(valor):
    return datetime.strptime(valor[:19], "%Y-%m-%dT%H:%M:%S") if valor else None


def leer_cfdi(xml_bytes):
    """Devuelve un dict con los datos del CFDI. Lanza CfdiInvalido si no es un CFDI timbrado."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True)
    try:
        raiz = etree.fromstring(xml_bytes, parser)
    except (etree.XMLSyntaxError, ValueError) as e:
        raise CfdiInvalido(f"No es un XML válido: {e}")

    if raiz.tag == f"{{{NS['cfdi4']}}}Comprobante":
        ns = NS["cfdi4"]
    elif raiz.tag == f"{{{NS['cfdi3']}}}Comprobante":
        ns = NS["cfdi3"]
    else:
        raise CfdiInvalido("El XML no es un cfdi:Comprobante 3.3 ni 4.0")
    c = {"cfdi": ns, "tfd": NS["tfd"]}

    timbre = raiz.find("cfdi:Complemento/tfd:TimbreFiscalDigital", c)
    if timbre is None or not timbre.get("UUID"):
        raise CfdiInvalido("El CFDI no tiene timbre fiscal (UUID)")

    emisor = raiz.find("cfdi:Emisor", c)
    receptor = raiz.find("cfdi:Receptor", c)
    if emisor is None or receptor is None:
        raise CfdiInvalido("El CFDI no tiene Emisor o Receptor")

    conceptos = [_leer_concepto(n, c) for n in raiz.findall("cfdi:Conceptos/cfdi:Concepto", c)]
    totales = _totales_impuestos(raiz, c, conceptos)

    relacionados = []
    for bloque in raiz.findall("cfdi:CfdiRelacionados", c):
        for rel in bloque.findall("cfdi:CfdiRelacionado", c):
            relacionados.append({"tipo_relacion": bloque.get("TipoRelacion"), "uuid": (rel.get("UUID") or "").upper()})

    return {
        "version": raiz.get("Version"),
        "uuid": timbre.get("UUID").upper(),
        "tipo_comprobante": raiz.get("TipoDeComprobante"),
        "serie": raiz.get("Serie") or "",
        "folio": raiz.get("Folio") or "",
        "fecha_emision": _fecha(raiz.get("Fecha")),
        "fecha_timbrado": _fecha(timbre.get("FechaTimbrado")),
        "rfc_emisor": (emisor.get("Rfc") or "").upper(),
        "nombre_emisor": emisor.get("Nombre") or "",
        "regimen_emisor": emisor.get("RegimenFiscal") or "",
        "rfc_receptor": (receptor.get("Rfc") or "").upper(),
        "nombre_receptor": receptor.get("Nombre") or "",
        "uso_cfdi": receptor.get("UsoCFDI") or "",
        "moneda": raiz.get("Moneda") or "MXN",
        "tipo_cambio": _num(raiz.get("TipoCambio"), 1.0),
        "subtotal": _num(raiz.get("SubTotal")),
        "descuento": _num(raiz.get("Descuento")),
        "total": _num(raiz.get("Total")),
        "metodo_pago": raiz.get("MetodoPago") or "",
        "forma_pago": raiz.get("FormaPago") or "",
        "conceptos": conceptos,
        "cfdi_relacionados": relacionados,
        **totales,
    }


def _leer_concepto(nodo, c):
    d = {
        "clave_prod_serv": nodo.get("ClaveProdServ") or "",
        "descripcion": nodo.get("Descripcion") or "",
        "cantidad": _num(nodo.get("Cantidad"), 1.0),
        "clave_unidad": nodo.get("ClaveUnidad") or "",
        "unidad": nodo.get("Unidad") or "",
        "valor_unitario": _num(nodo.get("ValorUnitario")),
        "importe": _num(nodo.get("Importe")),
        "descuento": _num(nodo.get("Descuento")),
        "iva": 0.0, "ieps": 0.0, "iva_retenido": 0.0, "isr_retenido": 0.0,
    }
    for t in nodo.findall("cfdi:Impuestos/cfdi:Traslados/cfdi:Traslado", c):
        if t.get("Impuesto") == IMPUESTO_IVA:
            d["iva"] += _num(t.get("Importe"))
        elif t.get("Impuesto") == IMPUESTO_IEPS:
            d["ieps"] += _num(t.get("Importe"))
    for r in nodo.findall("cfdi:Impuestos/cfdi:Retenciones/cfdi:Retencion", c):
        if r.get("Impuesto") == IMPUESTO_IVA:
            d["iva_retenido"] += _num(r.get("Importe"))
        elif r.get("Impuesto") == IMPUESTO_ISR:
            d["isr_retenido"] += _num(r.get("Importe"))
    for k in ("iva", "ieps", "iva_retenido", "isr_retenido"):
        d[k] = round(d[k], 2)
    return d


def _totales_impuestos(raiz, c, conceptos):
    """Totales del nodo cfdi:Impuestos del comprobante; si falta, suma de los conceptos."""
    imp = raiz.find("cfdi:Impuestos", c)
    if imp is not None and (imp.get("TotalImpuestosTrasladados") is not None or imp.get("TotalImpuestosRetenidos") is not None):
        iva = ieps = iva_ret = isr_ret = 0.0
        for t in imp.findall("cfdi:Traslados/cfdi:Traslado", c):
            if t.get("Impuesto") == IMPUESTO_IVA:
                iva += _num(t.get("Importe"))
            elif t.get("Impuesto") == IMPUESTO_IEPS:
                ieps += _num(t.get("Importe"))
        for r in imp.findall("cfdi:Retenciones/cfdi:Retencion", c):
            if r.get("Impuesto") == IMPUESTO_IVA:
                iva_ret += _num(r.get("Importe"))
            elif r.get("Impuesto") == IMPUESTO_ISR:
                isr_ret += _num(r.get("Importe"))
    else:
        iva = sum(x["iva"] for x in conceptos)
        ieps = sum(x["ieps"] for x in conceptos)
        iva_ret = sum(x["iva_retenido"] for x in conceptos)
        isr_ret = sum(x["isr_retenido"] for x in conceptos)
    return {"iva_trasladado": round(iva, 2), "ieps": round(ieps, 2), "iva_retenido": round(iva_ret, 2), "isr_retenido": round(isr_ret, 2)}
