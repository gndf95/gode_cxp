"""Lotes de pago: qué se paga, en qué transferencias, y el archivo TEF.

Un lote es una sola naturaleza (06 Banamex→Banamex, 12 interbancario) y una sola transferencia por
proveedor y cuenta bancaria; cada transferencia cubre una o varias facturas. Generar o transmitir un
lote NO mueve saldos ni crea pagos: eso pasa sólo al aplicar el resultado del banco (Task 7).
"""
import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, today

from gode_cxp.pagos.tef import TefInvalido, generar_tef, nombre_archivo

# Estados en los que un lote todavía "aparta" sus facturas: mientras el lote esté en uno de ellos,
# ninguna de sus facturas puede entrar a otro lote.
ACTIVOS = ("Preparado", "Autorizado", "Exportado", "Transmitido")


def _conf():
    conf = frappe.get_doc("Configuracion CxP")
    for campo in ("contrato_banamex", "cuenta_cargo_sucursal", "cuenta_cargo_numero", "nombre_empresa_tef",
                  "concepto_tef", "cuenta_bancaria_empresa"):
        if not conf.get(campo):
            frappe.throw(_("Configuración CxP: falta {0} (correr configurar_pagos).").format(campo))
    return conf


def _referencia_numerica(conf, fecha_pago):
    if conf.referencia_numerica_modo == "Fija":
        return (conf.referencia_numerica_fija or "").zfill(7)[-7:]
    return "0" + getdate(fecha_pago).strftime("%d%m%y")


def facturas_pagables(company, proveedor=None, hasta_vencimiento=None):
    """Facturas aprobadas, con saldo, en pesos y libres (ningún lote vivo las tiene apartadas)."""
    filtros = {"company": company, "docstatus": 1, "estado_revision": "Aprobada", "on_hold": 0,
               "outstanding_amount": [">", 0], "currency": "MXN", "en_lote": ["is", "not set"], "is_return": 0}
    if proveedor:
        filtros["supplier"] = proveedor
    if hasta_vencimiento:
        filtros["due_date"] = ["<=", hasta_vencimiento]
    facturas = frappe.get_all("Purchase Invoice", filters=filtros, order_by="due_date, name",
                              fields=["name", "supplier", "supplier_name", "bill_no", "bill_date", "due_date",
                                      "outstanding_amount", "cfdi_uuid"])
    # Un lote Preparado (borrador) todavía no escribe en_lote: sus facturas se excluyen por la tabla hija.
    en_borrador = set(frappe.get_all("Lote de Pago Factura", filters={"parenttype": "Lote de Pago", "docstatus": 0},
                                     pluck="factura"))
    bloqueados = set(frappe.get_all("Supplier", filters={"bloqueado_pagos": 1}, pluck="name"))
    return [f for f in facturas if f.name not in en_borrador and f.supplier not in bloqueados]


def _cuenta_del_proveedor(proveedor):
    """La cuenta con CLABE del proveedor; si tiene varias, la marcada como predeterminada."""
    cuentas = frappe.get_all("Bank Account",
                             filters={"party_type": "Supplier", "party": proveedor, "disabled": 0, "clabe": ["!=", ""]},
                             fields=["name", "is_default", "verificada", "tipo_pago_tef", "clabe",
                                     "sucursal_banamex", "cuenta_banamex", "nombre_tef"],
                             order_by="is_default desc, modified desc")
    if not cuentas:
        frappe.throw(_("El proveedor {0} no tiene cuenta bancaria con CLABE.").format(proveedor))
    cuenta = cuentas[0]
    if cuenta.tipo_pago_tef not in ("06", "12"):
        frappe.throw(_("La cuenta {0} de {1} no tiene naturaleza TEF: vuelve a guardar la CLABE.")
                     .format(cuenta.name, proveedor))
    return cuenta


def crear_lotes(company, fecha_pago, partidas):
    """partidas = [{"factura": name, "importe": float}]. Devuelve los nombres de los lotes creados
    (uno por naturaleza, porque un archivo del banco no mezcla 06 con 12)."""
    conf = _conf()
    if isinstance(partidas, str):
        partidas = frappe.parse_json(partidas)
    if not partidas:
        frappe.throw(_("No se eligió ninguna factura."))
    fecha_pago = getdate(fecha_pago)
    por_transferencia = {}   # (proveedor, cuenta) -> {"cuenta": dict, "facturas": [...]}
    for p in partidas:
        pi = frappe.db.get_value("Purchase Invoice", p["factura"],
                                 ["name", "supplier", "bill_no", "cfdi_uuid", "outstanding_amount"], as_dict=True)
        if not pi:
            frappe.throw(_("No existe la factura {0}.").format(p["factura"]))
        cuenta = _cuenta_del_proveedor(pi.supplier)
        clave = (pi.supplier, cuenta.name)
        por_transferencia.setdefault(clave, {"cuenta": cuenta, "facturas": []})["facturas"].append(
            {"factura": pi.name, "proveedor": pi.supplier, "folio": pi.bill_no, "uuid": pi.cfdi_uuid,
             "saldo_al_crear": pi.outstanding_amount, "importe": flt(p["importe"])})
    por_naturaleza = {}
    for (proveedor, _nombre_cuenta), datos in por_transferencia.items():
        c = datos["cuenta"]
        cuenta_tef = c.clabe if c.tipo_pago_tef == "12" else f"{c.sucursal_banamex}{c.cuenta_banamex}"
        por_naturaleza.setdefault(c.tipo_pago_tef, []).append((
            {"proveedor": proveedor, "cuenta_bancaria": c.name, "beneficiario_tef": c.nombre_tef,
             "cuenta_tef": cuenta_tef, "importe": sum(f["importe"] for f in datos["facturas"])}, datos["facturas"]))
    nombres = []
    for naturaleza, transferencias in sorted(por_naturaleza.items()):
        lote = frappe.new_doc("Lote de Pago")
        lote.update({"company": company, "fecha_pago": fecha_pago, "naturaleza": naturaleza,
                     "concepto": conf.concepto_tef, "referencia_numerica": _referencia_numerica(conf, fecha_pago),
                     "cuenta_bancaria_empresa": conf.cuenta_bancaria_empresa})
        for idx, (t, facturas) in enumerate(transferencias, start=1):
            lote.append("transferencias", t)
            for f in facturas:
                lote.append("facturas", dict(f, transferencia=idx))
        lote.insert()   # validar_lote corre aquí (hook validate)
        nombres.append(lote.name)
    return nombres


def _siguiente_secuencial(fecha_pago):
    # FOR UPDATE: dos usuarios generando a la vez el mismo día no deben tomar el mismo número.
    fila = frappe.db.sql("""select max(secuencial) from `tabLote de Pago`
                            where fecha_pago = %s and estado_lote != 'Cancelado' for update""", (fecha_pago,))
    return int(fila[0][0] or 0) + 1


def _lote_como_dict(lote, conf):
    """El lote como lo quiere pagos.tef.generar_tef (un dict plano, sin frappe)."""
    transferencias = []
    for t in lote.transferencias:
        c = frappe.db.get_value("Bank Account", t.cuenta_bancaria,
                                ["clabe", "sucursal_banamex", "cuenta_banamex"], as_dict=True)
        d = {"importe": t.importe, "beneficiario": t.beneficiario_tef}
        if lote.naturaleza == "12":
            d["clabe"] = c.clabe
        else:
            d["sucursal"], d["cuenta"] = c.sucursal_banamex, c.cuenta_banamex
        transferencias.append(d)
    return {"contrato": conf.contrato_banamex, "fecha": getdate(lote.fecha_pago), "secuencial": lote.secuencial,
            "empresa": conf.nombre_empresa_tef, "concepto": lote.concepto, "naturaleza": lote.naturaleza,
            "sucursal_cargo": conf.cuenta_cargo_sucursal, "cuenta_cargo": conf.cuenta_cargo_numero,
            "referencia_numerica": lote.referencia_numerica, "transferencias": transferencias}


def generar_archivo(lote_name):
    """Arma el archivo TEF del lote autorizado, lo adjunta y lo deja en Exportado.

    Se puede volver a generar mientras el lote no esté transmitido (por ejemplo si BancaNet rechazó
    el archivo): el adjunto anterior se reemplaza y el secuencial ya asignado no cambia."""
    lote = frappe.get_doc("Lote de Pago", lote_name)
    if lote.docstatus != 1 or lote.estado_lote not in ("Autorizado", "Exportado"):
        frappe.throw(_("El lote debe estar Autorizado (enviado) y no Transmitido para generar el archivo."))
    conf = _conf()
    # El secuencial se toma antes de armar el archivo (va dentro de él) pero se guarda después: si el
    # archivo no se puede armar, el número no se quema.
    secuencial = lote.secuencial or _siguiente_secuencial(lote.fecha_pago)
    lote.secuencial = secuencial
    try:
        datos = generar_tef(_lote_como_dict(lote, conf))
    except TefInvalido as e:
        frappe.throw(_("El archivo del banco no se pudo armar: {0}").format(e))
    nombre = nombre_archivo(getdate(lote.fecha_pago), secuencial, lote.naturaleza)
    # Se borra SÓLO el archivo TEF anterior (el que apunta `archivo_tef` y cualquiera que ya lleve
    # este mismo nombre): lo demás que Tesorería haya adjuntado al lote —el acuse de BancaNet, por
    # ejemplo— no se toca, y sin esto Frappe guardaría el nuevo como "170926-0001-12(1).txt".
    viejos = frappe.get_all("File", filters={"attached_to_doctype": "Lote de Pago", "attached_to_name": lote.name},
                            or_filters=[["file_url", "=", lote.archivo_tef or ""], ["file_name", "=", nombre]],
                            pluck="name")
    for viejo in viejos:
        frappe.delete_doc("File", viejo, ignore_permissions=True, force=1)
    archivo = frappe.get_doc({"doctype": "File", "file_name": nombre, "content": datos, "is_private": 1,
                              "attached_to_doctype": "Lote de Pago", "attached_to_name": lote.name}).insert(ignore_permissions=True)
    for i, t in enumerate(lote.transferencias, start=1):
        t.db_set("linea_tef", i)
    lote.db_set({"secuencial": secuencial, "archivo_tef": archivo.file_url, "nombre_archivo": nombre,
                 "generado_el": now_datetime(), "estado_lote": "Exportado"})
    return {"nombre_archivo": nombre, "file_url": archivo.file_url}


def marcar_transmitido(lote_name, autorizacion):
    """Tesorería subió el archivo a BancaNet y captura la autorización que dio el banco."""
    lote = frappe.get_doc("Lote de Pago", lote_name)
    if lote.estado_lote != "Exportado":
        frappe.throw(_("Solo se marca como transmitido un lote Exportado (con archivo generado)."))
    autorizacion = str(autorizacion or "").strip()
    if not autorizacion:
        frappe.throw(_("Captura la autorización que dio BancaNet."))
    lote.db_set({"autorizacion_banco": autorizacion, "transmitido_el": now_datetime(), "estado_lote": "Transmitido"})


def nuevo_lote_pendientes(lote_name):
    """Desde un lote Parcial o Rechazado, arma un lote nuevo (Preparado) con lo que el banco no pagó.

    Devuelve None si no quedaba nada por reintentar. El lote viejo NO se cancela: es el historial de
    lo que se mandó al banco."""
    lote = frappe.get_doc("Lote de Pago", lote_name)
    if lote.estado_lote not in ("Parcial", "Rechazado"):
        frappe.throw(_("Solo se reintenta un lote Parcial o Rechazado."))
    pendientes = [t for t in lote.transferencias
                  if t.estado_pago in ("Pendiente", "Rechazado", "Devuelto") and not t.reintentado_en]
    if not pendientes:
        return None
    conf = _conf()
    nuevo = frappe.new_doc("Lote de Pago")
    nuevo.update({"company": lote.company, "fecha_pago": today(), "naturaleza": lote.naturaleza,
                  "concepto": lote.concepto, "referencia_numerica": _referencia_numerica(conf, today()),
                  "cuenta_bancaria_empresa": lote.cuenta_bancaria_empresa, "lote_origen": lote.name})
    for idx, t in enumerate(pendientes, start=1):
        nuevo.append("transferencias", {"proveedor": t.proveedor, "cuenta_bancaria": t.cuenta_bancaria,
                                        "beneficiario_tef": t.beneficiario_tef, "cuenta_tef": t.cuenta_tef,
                                        "importe": t.importe})
        for f in lote.facturas:
            if f.transferencia == t.idx:
                nuevo.append("facturas", {"transferencia": idx, "proveedor": f.proveedor, "factura": f.factura,
                                          "folio": f.folio, "uuid": f.uuid, "importe": f.importe,
                                          "saldo_al_crear": frappe.db.get_value("Purchase Invoice", f.factura, "outstanding_amount")})
                # Se libera la factura del lote viejo para que validar_lote no la vea "en otro lote".
                frappe.db.set_value("Purchase Invoice", f.factura, "en_lote", None)
    nuevo.insert()
    for t in pendientes:
        t.db_set("reintentado_en", nuevo.name)
    return nuevo.name
