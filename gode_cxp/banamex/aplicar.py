"""El resultado del banco sobre un lote: alta desde el lote (captura manual), carga del archivo que
devuelve BancaNet y las cuentas que se le sacan al guardar.

Crear los pagos (`Payment Entry`) a partir de un resultado es otra cosa y llega aparte: aquí no se
mueve ningún saldo. Formatos del banco: frappe-hr-ops/docs/banamex-formatos.md.
"""
import frappe
from frappe import _
from frappe.utils import flt

from gode_cxp.banamex.respuesta import RespuestaInvalida, leer_respuesta

CAPTURA_MANUAL, ARCHIVO_DEL_PORTAL = "Captura manual", "Archivo del portal"
# Estados del lote en los que ya tiene sentido capturar lo que contestó el banco: el archivo se subió.
CON_RESPUESTA = ("Transmitido", "Aplicado", "Parcial", "Rechazado")
# Un peso partido: por debajo de medio centavo no es una diferencia, es el redondeo del Currency.
TOLERANCIA = 0.005


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
    r.save()
    return r


def validar_resultado(doc, method=None):
    """Hook `validate` del Resultado Bancario: los totales, el cruce con el lote y las diferencias.

    No bloquea nada (un resultado con diferencias se guarda igual): deja por escrito qué no cuadra
    para que Tesorería lo mire antes de crear los pagos."""
    if not doc.lote:
        return          # el campo es obligatorio: que lo diga la validación de Frappe, no un error aquí
    lote = frappe.get_doc("Lote de Pago", doc.lote)
    aplicados = [m for m in doc.movimientos if m.estatus == "3"]
    doc.total_calculado = sum(flt(m.importe) for m in aplicados)
    doc.num_aplicados = len(aplicados)
    doc.num_rechazados = sum(1 for m in doc.movimientos if m.estatus == "5")
    por_linea = {(t.linea_tef or t.idx): t for t in lote.transferencias}
    diferencias = []
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
    if doc.estado not in ("Aplicado", "Revisado"):
        doc.estado = "Con diferencias" if diferencias else "Importado"
