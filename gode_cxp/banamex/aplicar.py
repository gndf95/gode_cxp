"""El resultado del banco sobre un lote: alta desde el lote (captura manual), carga del archivo que
devuelve BancaNet, las cuentas que se le sacan al guardar y —el único sitio de la app que mueve
saldos— la creación de los pagos (`Payment Entry`) de lo que el banco aplicó.

Formatos del banco: frappe-hr-ops/docs/banamex-formatos.md.
"""
import frappe
from erpnext.accounts.party import get_party_account
from frappe import _
from frappe.utils import flt, getdate, now_datetime, today

from gode_cxp.banamex.respuesta import CANCELADO, RECHAZADO, RespuestaInvalida, leer_respuesta
from gode_cxp.pagos.lotes import _sql_con_candado

CAPTURA_MANUAL, ARCHIVO_DEL_PORTAL = "Captura manual", "Archivo del portal"
# Estados del lote en los que ya tiene sentido capturar lo que contestó el banco: el archivo se subió.
CON_RESPUESTA = ("Transmitido", "Aplicado", "Parcial", "Rechazado")
# Un peso partido: por debajo de medio centavo no es una diferencia, es el redondeo del Currency.
TOLERANCIA = 0.005
# Estados de una transferencia que todavía admiten un pago: 'Aplicado' ya tiene el suyo.
SIN_PAGAR = ("Pendiente", "Rechazado", "Devuelto")
# Estados del lote que salen de lo que contestó el banco (los que recalcula `recalcular_estado_lote`).
DEL_BANCO = ("Aplicado", "Parcial", "Rechazado")
# Campos del resultado que sólo escriben la carga del archivo y la creación de los pagos.
DE_LA_APLICACION = ("estado", "aplicado_el", "aplicado_por")
# Lo mismo por movimiento; y, en una línea que ya tiene su pago, tampoco se toca lo que la describe.
DE_LA_APLICACION_MOV = ("accion", "pago")
CONGELADOS_CON_PAGO = ("estatus", "importe", "linea", "cuenta")


def crear_resultado_desde_lote(lote_name):
    """Un Resultado Bancario en borrador con un movimiento por transferencia del lote y el estatus
    vacío, para que Tesorería capture a mano el 3 / 5 y la clave de rastreo de cada línea."""
    lote = frappe.get_doc("Lote de Pago", lote_name)
    if lote.docstatus != 1 or lote.estado_lote not in CON_RESPUESTA:
        frappe.throw(_("Solo se captura el resultado de un lote ya transmitido al banco (el lote {0} "
                       "está en '{1}').").format(lote.name, lote.estado_lote))
    r = frappe.new_doc("Resultado Bancario")
    r.update({"lote": lote.name, "autorizacion": lote.autorizacion_banco, "origen": CAPTURA_MANUAL})
    for t in lote.transferencias:
        # `linea_tef` es la línea que ocupó la transferencia en el archivo (la asigna generar_archivo);
        # si el archivo aún no se generó, el idx de la tabla es el mismo orden.
        r.append("movimientos", {"linea": t.linea_tef or t.idx, "beneficiario": t.beneficiario_tef,
                                 "cuenta": t.cuenta_tef, "importe": t.importe, "estatus": ""})
    r.insert()
    _avisar_si_el_lote_ya_tiene_resultado(r)
    return r


def _contenido(file_url):
    """El archivo que subió Tesorería, en BYTES, y su nombre.

    Se lee del disco y la decodificación la hace el lector (`banamex/respuesta.py`): `get_content()`
    decide por su cuenta si el archivo es texto y con qué juego de caracteres, y con eso un acento del
    mensaje del banco se convierte en otra cosa. Y como el archivo trae nombres de proveedores y sus
    cuentas, se exige que sea privado y que quien lo carga tenga permiso de leerlo. Mismo criterio que
    pagos/preregistro.py."""
    name = frappe.db.get_value("File", {"file_url": file_url}, "name")
    if not name:
        frappe.throw(_("No se encontró el archivo {0} en el sistema.").format(file_url))
    archivo = frappe.get_doc("File", name)
    archivo.check_permission("read")
    if not archivo.is_private:
        frappe.throw(_("El archivo {0} es público: la respuesta del banco trae los datos bancarios de "
                       "los proveedores, así que hay que subirla como archivo privado.")
                     .format(archivo.file_name))
    with open(archivo.get_full_path(), "rb") as f:
        return f.read(), archivo.file_name


def cargar_archivo(resultado_name, file_url):
    """Lee el archivo del banco (CSV del portal o ancho fijo de exportación) y con él REEMPLAZA los
    movimientos del resultado. Lo que capturó Tesorería a mano se pierde a propósito: el archivo del
    banco es la versión del banco."""
    r = frappe.get_doc("Resultado Bancario", resultado_name)
    if r.estado == "Aplicado":
        frappe.throw(_("El resultado {0} ya se aplicó: para un archivo nuevo captura otro resultado "
                       "del lote.").format(r.name))
    datos, nombre = _contenido(file_url)
    try:
        leido = leer_respuesta(datos, nombre)
    except RespuestaInvalida as e:
        frappe.throw(_("El archivo {0} no se pudo leer: {1}").format(nombre, e))
    r.set("movimientos", [])
    for m in leido["movimientos"]:
        # El importe viene en Decimal: se pasa como texto para que Frappe lo convierta él mismo.
        r.append("movimientos", dict(m, importe=str(m["importe"])))
    total = leido["total_archivo"]
    r.update({"origen": ARCHIVO_DEL_PORTAL, "archivo": file_url,
              "estatus_archivo": leido["estatus_archivo"],
              "total_archivo": float(total) if total is not None else None,
              # El CSV del portal no trae la autorización: se conserva la que ya tenía el resultado.
              "autorizacion": leido["autorizacion"] or r.autorizacion,
              # Un archivo nuevo se vuelve a juzgar: el visto bueno anterior era de los movimientos viejos.
              "estado": "Importado"})
    # La guardia deja pasar este cambio de estado porque es la carga del archivo quien lo hace.
    r.flags.cargando = True
    r.save()
    _avisar_si_el_lote_ya_tiene_resultado(r)
    return r


def _distinto(a, b):
    """Dos valores del mismo campo, comparados como los guarda Frappe: un None y un '' son lo mismo,
    y un Currency es un número (1160 y 1160.0 no son un cambio)."""
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        return abs(flt(a) - flt(b)) > TOLERANCIA
    return str(a or "") != str(b or "")


def _guardia_de_lo_aplicado(doc):
    """Lo que escribe la aplicación de los pagos no se edita a mano.

    El DocType marca estos campos `read_only`, pero eso es del formulario: por la API (o por un
    `save()` de una consola) se escriben igual, y ahí lo que está en juego es dar una factura por
    pagada sin que exista el pago, o al revés. Las funciones de este módulo pasan por encima de la
    guardia con `db_set` (que no corre `validate`) o con `doc.flags`."""
    antes = doc.get_doc_before_save()
    if not antes:
        return          # un resultado nuevo: todavía no hay nada aplicado que proteger
    for campo in DE_LA_APLICACION:
        # `cargar_archivo` sí vuelve a dejar el estado en 'Importado': el archivo nuevo se rejuzga.
        if campo == "estado" and doc.flags.cargando:
            continue
        if _distinto(doc.get(campo), antes.get(campo)):
            frappe.throw(_("'{0}' lo escribe la aplicación de los pagos del banco, no se cambia a "
                           "mano (decía '{1}' y se intentó dejar '{2}').")
                         .format(_(doc.meta.get_label(campo)), antes.get(campo) or "", doc.get(campo) or ""))
    if antes.aplicado_el:
        for campo in ("lote", "estatus_archivo"):
            if _distinto(doc.get(campo), antes.get(campo)):
                frappe.throw(_("El resultado ya se aplicó: no se puede cambiar '{0}'.")
                             .format(_(doc.meta.get_label(campo))))
    previos = {m.name: m for m in antes.movimientos}
    for m in doc.movimientos:
        viejo = previos.get(m.name)
        if not viejo:
            continue    # una línea nueva: no puede traer nada de una aplicación que no ocurrió
        for campo in DE_LA_APLICACION_MOV:
            if _distinto(m.get(campo), viejo.get(campo)):
                frappe.throw(_("La línea {0} tiene '{1}' puesto por la aplicación de los pagos: no se "
                               "cambia a mano.").format(viejo.linea, _(m.meta.get_label(campo))))
        if viejo.pago:
            for campo in CONGELADOS_CON_PAGO:
                if _distinto(m.get(campo), viejo.get(campo)):
                    frappe.throw(_("La línea {0} ya tiene el pago {1}: editarla no deshace el pago, "
                                   "sólo deja el resultado mintiendo. Para deshacerlo, cancela el "
                                   "pago.").format(viejo.linea, viejo.pago))
    vivas = {m.name for m in doc.movimientos}
    for viejo in antes.movimientos:
        if viejo.pago and viejo.name not in vivas:
            frappe.throw(_("La línea {0} ya tiene el pago {1}: no se puede quitar del resultado. Para "
                           "deshacerlo, cancela el pago.").format(viejo.linea, viejo.pago))


def _otro_resultado_aplicado(doc):
    """Otro Resultado Bancario del mismo lote que ya creó pagos, si lo hay.

    Un segundo archivo del banco sobre el mismo lote está permitido (puede llegar una corrección),
    pero conviene decirlo: lo que ya se pagó no se vuelve a pagar (de eso se encarga la idempotencia
    por transferencia de `aplicar_resultado`), así que el segundo resultado siempre va a "faltarle"
    algo respecto de lo que dice el archivo."""
    if not doc.lote:
        return None
    # Se pregunta por `aplicado_el` y no por el estado: un resultado que creó pagos y además traía
    # diferencias queda en 'Con diferencias' (y puede acabar en 'Revisado'), no en 'Aplicado'.
    return frappe.db.get_value("Resultado Bancario",
                               {"lote": doc.lote, "aplicado_el": ("is", "set"),
                                "name": ("!=", doc.name or "")}, "name")


def _avisar_si_el_lote_ya_tiene_resultado(doc):
    """El aviso en pantalla de lo mismo que queda escrito en `diferencias`: quien está capturando el
    segundo resultado de un lote tiene que enterarse ahí mismo, no al leer el campo después."""
    otro = _otro_resultado_aplicado(doc)
    if otro:
        frappe.msgprint(_("El lote {0} ya tiene el resultado {1} aplicado. Puedes seguir: lo que ya "
                          "se pagó no se volverá a pagar, sólo se crearán los pagos que falten.")
                        .format(doc.lote, otro), title=_("El lote ya tiene un resultado aplicado"),
                        indicator="orange")


def validar_resultado(doc, method=None):
    """Hook `validate` del Resultado Bancario: los totales, el cruce con el lote y las diferencias.

    No bloquea nada de lo que Tesorería captura (un resultado con diferencias se guarda igual): deja
    por escrito qué no cuadra para que lo mire antes de crear los pagos. Lo único que sí bloquea es
    editar a mano lo que escribió la aplicación de los pagos."""
    if not doc.lote:
        return          # el campo es obligatorio: que lo diga la validación de Frappe, no un error aquí
    if not doc.flags.aplicando:
        _guardia_de_lo_aplicado(doc)
    lote = frappe.get_doc("Lote de Pago", doc.lote)
    aplicados = [m for m in doc.movimientos if m.estatus == "3"]
    doc.total_calculado = sum(flt(m.importe) for m in aplicados)
    doc.num_aplicados = len(aplicados)
    doc.num_rechazados = sum(1 for m in doc.movimientos if m.estatus == "5")
    if doc.flags.aplicando:
        # `aplicar_resultado` manda: ya cruzó cada movimiento con su transferencia (sabe cruzar por
        # cuenta, no sólo por línea), ya juntó las diferencias —incluidas las de crear los pagos, que
        # este hook no puede conocer— y ya decidió el estado final. Los tres totales de arriba sí se
        # recalculan: son un resumen de los movimientos y no dependen de lo que hizo la aplicación.
        return
    por_linea = {(t.linea_tef or t.idx): t for t in lote.transferencias}
    diferencias = []
    if doc.estatus_archivo in (RECHAZADO, CANCELADO):
        diferencias.append(_("el banco rechazó o canceló el archivo completo (estatus {0}): ninguna "
                             "transferencia se pagó").format(doc.estatus_archivo))
    otro = _otro_resultado_aplicado(doc)
    if otro:
        diferencias.append(_("el lote ya tiene el resultado {0} aplicado: de este sólo se crearán los "
                             "pagos que falten (una transferencia ya pagada no se vuelve a pagar)")
                           .format(otro))
    # El total del archivo es el de control del registro 4, o sea TODO lo que se mandó: se compara con
    # el total del lote, no con lo aplicado (que es menos si el banco rechazó alguna transferencia).
    if doc.total_archivo and abs(flt(doc.total_archivo) - flt(lote.total_lote)) > TOLERANCIA:
        diferencias.append(_("el total del archivo ({0}) no coincide con el del lote ({1})")
                           .format(doc.total_archivo, lote.total_lote))
    if len(doc.movimientos) != len(lote.transferencias):
        diferencias.append(_("el resultado trae {0} movimientos y el lote tiene {1} transferencias")
                           .format(len(doc.movimientos), len(lote.transferencias)))
    for m in doc.movimientos:
        t = por_linea.get(m.linea)
        # Con qué transferencia del lote se cruzó cada movimiento: es lo que usará la creación de pagos.
        m.transferencia_idx = t.idx if t else None
        if not t:
            diferencias.append(_("la línea {0} del banco no corresponde a ninguna transferencia del lote")
                               .format(m.linea))
        elif abs(flt(t.importe) - flt(m.importe)) > TOLERANCIA:
            diferencias.append(_("línea {0}: importe {1} en el banco, {2} en el lote")
                               .format(m.linea, m.importe, t.importe))
    doc.diferencias = "\n".join(diferencias)
    # 'Aplicado' y 'Revisado' los pone una persona o la creación de los pagos: guardar no los deshace.
    if not doc.aplicado_el and doc.estado not in ("Aplicado", "Revisado"):
        doc.estado = "Con diferencias" if diferencias else "Importado"


# --- Aplicar el resultado: crear los pagos ------------------------------------------------------


def _conf_pagos():
    conf = frappe.get_doc("Configuracion CxP")
    if not conf.cuenta_banco_erp or not conf.modo_pago_transferencia:
        frappe.throw(_("Configuración CxP: falta la cuenta contable del banco o el modo de pago de "
                       "las transferencias (correr configurar_pagos)."))
    return conf


def _solo_digitos(valor):
    """Una cuenta comparable: el banco la devuelve con ceros de relleno a 20 y el lote la guarda
    tal cual (CLABE de 18 o sucursal+cuenta Banamex de 11)."""
    return "".join(c for c in str(valor or "") if c.isdigit()).lstrip("0")


def _cruzar(lote, m):
    """Con qué transferencia del lote corresponde un movimiento del banco, o None.

    Primero por la línea del archivo (que es lo que el banco devuelve y lo que identifica sin
    ambigüedad); si esa línea no existe o su cuenta no es la misma, por (cuenta, importe) y sólo si
    hay UN candidato: con dos transferencias iguales no hay manera de saber cuál pagó el banco."""
    por_linea = {(t.linea_tef or t.idx): t for t in lote.transferencias}
    cuenta = _solo_digitos(m.cuenta)
    t = por_linea.get(int(m.linea or 0))
    if t and (not cuenta or _solo_digitos(t.cuenta_tef) == cuenta):
        return t
    candidatos = [x for x in lote.transferencias
                  if cuenta and _solo_digitos(x.cuenta_tef) == cuenta
                  and abs(flt(x.importe) - flt(m.importe)) <= TOLERANCIA]
    return candidatos[0] if len(candidatos) == 1 else None


def _cuenta_por_pagar(facturas, proveedor, company):
    """La cuenta contable a la que se abona el pago: la MISMA `credit_to` de sus facturas.

    ERPNext exige que el `paid_to` del pago sea exactamente el `credit_to` de cada factura referida
    (`PaymentEntry.validate_reference_documents`), así que no basta con la cuenta por omisión del
    proveedor: si las facturas de una transferencia no comparten cuenta por pagar, no hay un solo
    pago posible y se dice en español en vez de dejar salir el mensaje de ERPNext."""
    cuentas = {frappe.db.get_value("Purchase Invoice", f.factura, "credit_to") for f in facturas}
    cuentas.discard(None)
    if len(cuentas) > 1:
        frappe.throw(_("Las facturas de esta transferencia usan cuentas por pagar distintas ({0}): "
                       "no se pueden pagar en una sola transferencia.").format(", ".join(sorted(cuentas))))
    return cuentas.pop() if cuentas else get_party_account("Supplier", proveedor, company)


def _autorizacion_del_movimiento(lote, r, m):
    """La del movimiento si el banco la devolvió por transferencia (layout C de exportación), si no
    la del resultado, si no la que se capturó al transmitir el lote."""
    return (m.autorizacion or r.autorizacion or lote.autorizacion_banco or "").strip()


def _crear_pago(lote, t, facturas, m, conf, autorizacion):
    """El Payment Entry de UNA transferencia, con una asignación exacta por factura.

    Se arma a mano y no con `get_payment_entry` porque una transferencia puede cubrir varias facturas
    con importes parciales y `get_payment_entry` asigna el saldo completo de una sola. No se llama
    `set_missing_values()` aparte: `PaymentEntry.validate` ya lo corre (con
    `setup_party_account_field()` antes, que es quien deja `party_account_field` en su sitio), así que
    las monedas y los tipos de cambio los llena `insert()`."""
    if not facturas or abs(sum(flt(f.importe) for f in facturas) - flt(t.importe)) > TOLERANCIA:
        frappe.throw(_("Las asignaciones a facturas deben sumar el importe de la transferencia {0}; "
                       "no se puede crear un pago sin facturas o con importes distintos.").format(t.idx))
    pe = frappe.new_doc("Payment Entry")
    pe.update({"payment_type": "Pay", "party_type": "Supplier", "party": t.proveedor,
               "company": lote.company, "posting_date": lote.fecha_pago,
               "mode_of_payment": conf.modo_pago_transferencia, "paid_from": conf.cuenta_banco_erp,
               "paid_to": _cuenta_por_pagar(facturas, t.proveedor, lote.company),
               "paid_amount": flt(t.importe), "received_amount": flt(t.importe),
               "reference_no": f"{autorizacion}-{int(lote.secuencial or 0):04d}-{int(m.linea or t.idx):03d}",
               "reference_date": lote.fecha_pago, "bank_account": lote.cuenta_bancaria_empresa,
               "lote_pago": lote.name, "autorizacion_banco": autorizacion,
               "clave_rastreo": m.clave_rastreo})
    for f in facturas:
        pe.append("references", {"reference_doctype": "Purchase Invoice", "reference_name": f.factura,
                                 "allocated_amount": flt(f.importe)})
    pe.flags.ignore_permissions = True
    pe.insert()
    pe.submit()
    return pe


def _liberar_facturas_con_saldo(lote, transferencia=None):
    """Cada transferencia pagada libera su saldo restante para otro lote; al cerrar el lote se
    repite para todas como respaldo. Las facturas en cero conservan el rastro de su lote."""
    for f in lote.facturas:
        if transferencia is not None and f.transferencia != transferencia:
            continue
        # El UPDATE condicional comprueba el dueño actual, incluso con un snapshot anterior.
        frappe.db.sql("""update `tabPurchase Invoice` set en_lote = NULL
                         where name = %s and en_lote = %s and outstanding_amount > 0""",
                      (f.factura, lote.name))


def recalcular_estado_lote(lote_name):
    """El estado del lote sale de sus transferencias: Aplicado (todas), Rechazado (ninguna aplicada y
    alguna rechazada o devuelta), Parcial (mezcla). Si el banco todavía no contestó nada, se queda
    como está; y si se cancelaron todos los pagos de un lote que ya estaba resuelto, vuelve a
    Transmitido, que es lo único cierto sobre él.

    Se escribe con `db_set`: `estado_lote` está blindado contra `save()` en un lote enviado
    (pagos/eventos.validar_cambios_del_lote_enviado)."""
    lote = frappe.get_doc("Lote de Pago", lote_name)
    estados = {t.estado_pago for t in _sql_con_candado(
        """select estado_pago from `tabLote de Pago Transferencia`
           where parent = %s and parenttype = 'Lote de Pago' order by idx for update""",
        (lote.name,), as_dict=True)}
    if estados == {"Aplicado"}:
        nuevo = "Aplicado"
    elif "Aplicado" in estados:
        nuevo = "Parcial"
    elif estados & {"Rechazado", "Devuelto"}:
        nuevo = "Rechazado"
    elif lote.estado_lote in DEL_BANCO:
        nuevo = "Transmitido"
    else:
        nuevo = lote.estado_lote
    if nuevo != lote.estado_lote:
        lote.db_set("estado_lote", nuevo)
    if nuevo == "Aplicado":
        _liberar_facturas_con_saldo(lote)
    return nuevo


def aplicar_resultado(resultado_name):
    """Crea y envía un Payment Entry por cada transferencia que el banco aplicó, marca los rechazos y
    cierra el lote. Es el único punto de la app que mueve saldos.

    Idempotente: una transferencia que ya está Aplicado con su pago no se vuelve a pagar nunca, y dos
    aplicaciones a la vez las separa el candado del lote."""
    r = frappe.get_doc("Resultado Bancario", resultado_name)
    lote = frappe.get_doc("Lote de Pago", r.lote)
    if lote.docstatus != 1 or lote.estado_lote not in CON_RESPUESTA:
        frappe.throw(_("Solo se aplican los pagos de un lote ya transmitido al banco (el lote {0} "
                       "está en '{1}').").format(lote.name, lote.estado_lote))
    # Candado sobre la fila del lote: la idempotencia se apoya en leer `estado_pago` de cada
    # transferencia, así que dos aplicaciones simultáneas (dos resultados del mismo lote, o dos clics)
    # leerían las dos 'Pendiente' y crearían dos pagos. Con el candado la segunda espera a la primera
    # —o MariaDB rechaza su FOR UPDATE y el helper lo dice en español—, y después vuelve a leer.
    _sql_con_candado("select name from `tabLote de Pago` where name = %s for update", (lote.name,))
    estados = {t.idx: t for t in _sql_con_candado(
        """select name, idx, estado_pago, pago, reintentado_en from `tabLote de Pago Transferencia`
           where parent = %s and parenttype = 'Lote de Pago' order by idx for update""",
        (lote.name,), as_dict=True)}
    # reload por sí solo conserva el snapshot de REPEATABLE READ. Las lecturas con candado
    # aportan también el modified y los movimientos actuales para guardar y cruzar sin pisarlos.
    actuales = _sql_con_candado(
        "select * from `tabResultado Bancario` where name = %s for update", (r.name,), as_dict=True)
    movimientos = _sql_con_candado(
        """select * from `tabResultado Bancario Movimiento`
           where parent = %s and parenttype = 'Resultado Bancario' order by idx for update""",
        (r.name,), as_dict=True)
    r.reload()
    r.update(actuales[0])
    r.set("movimientos", movimientos)
    if r.lote != lote.name:
        frappe.throw(_("Otra persona cambió el lote del resultado: vuelve a abrirlo e inténtalo de nuevo."))
    lote.reload()
    if r.estatus_archivo in (RECHAZADO, CANCELADO):
        diferencias = [_("el archivo está rechazado/cancelado por el banco pero la línea {0} viene "
                         "como aplicada").format(m.linea) for m in r.movimientos if m.estatus == "3"]
        frappe.throw("\n".join(diferencias) or _("El archivo está rechazado/cancelado por el banco: "
                                                "no se pueden crear pagos."))
    if r.estado not in ("Importado", "Revisado", "Con diferencias"):
        frappe.throw(_("Solo se aplica un resultado Importado, Revisado o Con diferencias; está en '{0}'.")
                     .format(r.estado))
    if r.estado == "Con diferencias" and not any(m.pago for m in r.movimientos):
        frappe.throw(_("El resultado tiene diferencias: usa Marcar revisado antes de aplicar los pagos."))
    if getdate(lote.fecha_pago) > getdate(today()):
        frappe.throw(_("La fecha del lote es futura: el pago aún no ocurre."))
    autorizacion_lote = (lote.autorizacion_banco or r.autorizacion or "").strip()
    if not autorizacion_lote:
        frappe.throw(_("Falta la autorización que dio BancaNet (en el lote o en el resultado)."))
    if not lote.autorizacion_banco:
        lote.db_set("autorizacion_banco", autorizacion_lote)
    conf = _conf_pagos()
    facturas_por_t = {}
    for f in lote.facturas:
        facturas_por_t.setdefault(f.transferencia, []).append(f)
    out = {"creados": [], "ya_aplicados": 0, "rechazados": 0, "sin_coincidencia": 0}
    diferencias = [r.diferencias] if r.diferencias else []
    for i, m in enumerate(r.movimientos):
        # Un savepoint por movimiento: el banco pudo aplicar cinco de seis transferencias y un pago que
        # no se puede crear no debe tirar los otros cinco ni dejar el lote a medio marcar.
        sp = f"resb_{i}"
        frappe.db.savepoint(sp)
        try:
            t = _cruzar(lote, m)
            if not t:
                m.accion = "Sin coincidencia"
                out["sin_coincidencia"] += 1
                continue
            m.transferencia_idx = t.idx
            if abs(flt(t.importe) - flt(m.importe)) > TOLERANCIA:
                # No se paga de menos ni de más que lo que autorizó el lote: se deja anotado y se ve.
                m.accion = "Importe distinto"
                diferencias.append(_("línea {0}: el banco reporta {1} y la transferencia es de {2}: "
                                     "no se creó el pago").format(m.linea, m.importe, t.importe))
                continue
            actual = estados[t.idx]
            if actual.reintentado_en:
                m.accion, m.pago = "Ya aplicado", actual.pago
                out["ya_aplicados"] += 1
                diferencias.append(_("línea {0}: la transferencia se reintentó en {1}; no se paga "
                                     "desde el lote origen").format(m.linea, actual.reintentado_en))
                continue
            if m.estatus == "3":
                if actual.estado_pago not in SIN_PAGAR or actual.pago:
                    # Ya tiene su pago: es el candado que evita el pago doble al aplicar dos veces.
                    m.accion, m.pago = "Ya aplicado", actual.pago
                    out["ya_aplicados"] += 1
                    continue
                autorizacion = _autorizacion_del_movimiento(lote, r, m)
                pago = _crear_pago(lote, t, facturas_por_t.get(t.idx, []), m, conf, autorizacion)
                t.db_set({"estado_pago": "Aplicado", "pago": pago.name,
                          "clave_rastreo": m.clave_rastreo or None, "motivo_rechazo": None})
                _liberar_facturas_con_saldo(lote, t.idx)
                actual.estado_pago, actual.pago = "Aplicado", pago.name
                m.accion, m.pago = "Pago creado", pago.name
                out["creados"].append(pago.name)
            elif m.estatus == "5":
                if actual.estado_pago not in SIN_PAGAR or actual.pago:
                    # No se degrada un pago que ya existe: eso lo hace cancelar el Payment Entry.
                    m.accion, m.pago = "Ya aplicado", actual.pago
                    out["ya_aplicados"] += 1
                    diferencias.append(_("línea {0}: el banco la reporta rechazada pero ya tiene el "
                                         "pago {1}").format(m.linea, actual.pago))
                    continue
                t.db_set({"estado_pago": "Rechazado", "motivo_rechazo": (m.motivo or "")[:140]})
                actual.estado_pago = "Rechazado"
                m.accion = "Rechazado"
                out["rechazados"] += 1
            else:
                m.accion = None     # el banco no dijo nada de esa línea: no se toca
        except Exception as e:
            frappe.db.rollback(save_point=sp)
            # El error se guarda en el movimiento Y en las diferencias: si sólo fuera al movimiento, el
            # resultado quedaría 'Aplicado' y nadie se enteraría de que falta un pago.
            frappe.log_error(title=f"Resultado bancario {r.name}: línea {m.linea}",
                             message=frappe.get_traceback())
            frappe.clear_last_message()
            m.accion = "Error al pagar"
            m.motivo = " | ".join(filter(None, (m.motivo, _("error al crear el pago: {0}").format(e))))
            out["sin_coincidencia"] += 1
            diferencias.append(_("línea {0}: el pago no se pudo crear ({1})").format(m.linea, e))
    out["estado_lote"] = recalcular_estado_lote(lote.name)
    r.diferencias = "\n".join(d for d in diferencias if d)
    r.estado = "Con diferencias" if r.diferencias else "Aplicado"
    r.aplicado_el, r.aplicado_por = now_datetime(), frappe.session.user
    # La bandera le dice al hook `validate` que no vuelva a calcular las diferencias ni el estado.
    r.flags.aplicando = True
    r.save(ignore_permissions=True)
    out["resumen"] = _("Pagos creados: {0} · ya aplicados: {1} · rechazados: {2} · sin coincidencia: "
                       "{3}. El lote quedó {4}.").format(
        len(out["creados"]), out["ya_aplicados"], out["rechazados"], out["sin_coincidencia"],
        out["estado_lote"])
    return out


def al_cancelar_pago(doc, method=None):
    """Hook `on_cancel` de Payment Entry: sólo actúa sobre los pagos que nacieron de un lote.

    Corre DESPUÉS del `on_cancel` de ERPNext (que ya le devolvió el saldo a las facturas) y ANTES del
    chequeo de enlaces de Frappe (`run_post_save_methods` llama a `check_no_back_links_exist` justo
    después de los hooks), así que limpiar `pago` aquí es además lo que permite cancelar el pago de un
    lote enviado: si no, el Link de la transferencia lo bloquearía.

    Si la factura liberada ya está en otro lote, no se recupera en_lote al cancelar el pago:
    se conserva la reserva del nuevo lote (decisión asumida)."""
    if not doc.get("lote_pago"):
        return
    filas = frappe.db.sql("""select name, idx from `tabLote de Pago Transferencia`
                             where parent = %s and parenttype = 'Lote de Pago' and pago = %s""",
                          (doc.lote_pago, doc.name), as_dict=True)
    if not filas:
        return
    for fila in filas:
        frappe.db.set_value("Lote de Pago Transferencia", fila.name,
                            {"estado_pago": "Pendiente", "pago": None, "clave_rastreo": None})
    # El lote vuelve a apartar las facturas de esa transferencia si nadie más las tiene: al quedar
    # Aplicado se habían liberado las que conservaban saldo, y sin `en_lote` aparecerían como pagables
    # aunque el lote las siga reclamando.
    idx = {fila.idx for fila in filas}
    lote = frappe.get_doc("Lote de Pago", doc.lote_pago)
    for f in lote.facturas:
        if f.transferencia in idx and not frappe.db.get_value("Purchase Invoice", f.factura, "en_lote"):
            frappe.db.set_value("Purchase Invoice", f.factura, "en_lote", lote.name)
    recalcular_estado_lote(doc.lote_pago)
