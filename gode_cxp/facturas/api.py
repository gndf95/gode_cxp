"""Puntos de entrada desde la interfaz: subir XML/ZIP y crear la factura de un CFDI."""
import io
import os
import posixpath
import zipfile

import frappe
from frappe import _

from gode_cxp.facturas.crear_factura import crear_factura_desde_cfdi
from gode_cxp.facturas.recepcion import procesar_xml

# Los métodos de abajo son whitelisted: todo lo que llega de fuera se valida aquí.
ORIGENES = ("SAT", "Carga manual", "Correo")          # opciones del campo CFDI Recibido.origen
CARPETAS_DEL_SITIO = ("/files/", "/private/files/")   # lo único que se acepta como file_url
MAX_ENTRADAS_ZIP = 200                                # archivos dentro de un ZIP
MAX_BYTES_MIEMBRO = 25 * 1024 * 1024                  # tamaño descomprimido de un archivo del ZIP
MAX_BYTES_ZIP = 100 * 1024 * 1024                     # tamaño descomprimido de todo el ZIP


@frappe.whitelist()
def procesar_archivos(file_urls, origen="Carga manual"):
    """Lee los XML (sueltos o dentro de ZIP) que el usuario acaba de subir y devuelve el resumen.
    Un archivo con problemas no detiene a los demás: se anota en 'errores' y se sigue."""
    frappe.has_permission("CFDI Recibido", "create", throw=True)
    if origen not in ORIGENES:
        frappe.throw(_("Origen no válido: {0}").format(origen))
    if isinstance(file_urls, str):
        file_urls = frappe.parse_json(file_urls)
    if not isinstance(file_urls, (list, tuple)):
        frappe.throw(_("Se esperaba una lista de archivos."))

    resultado = {"nuevos": [], "duplicados": [], "ajenos": [], "errores": [], "facturas": []}
    temporales = []
    for url in file_urls:
        try:
            archivo = _archivo_del_sitio(url)
        except Exception as e:
            _anotar_error(resultado, str(url), e)
            continue
        temporales.append(archivo.file_url)
        nombre = archivo.file_name or posixpath.basename(archivo.file_url or "")
        try:
            partes = _desempacar(nombre, _a_bytes(archivo.get_content()))
        except Exception as e:
            _anotar_error(resultado, nombre, e)
            partes = []
        for xml_nombre, xml_bytes, pdf_bytes in partes:
            _procesar_uno(xml_nombre, xml_bytes, pdf_bytes, origen, resultado)
    _borrar_temporales(temporales)
    return resultado


def _borrar_temporales(urls):
    """El archivo de la subida es temporal: el XML (y el PDF) definitivos ya quedaron adjuntos al
    CFDI Recibido desde recepcion.procesar_xml. Se borra al final y buscando por file_url porque
    Frappe le da el MISMO file_url a dos subidas con el mismo contenido
    (File.validate_duplicate_entry): borrando dentro del bucle, el segundo archivo del lote se
    quedaría sin nada que leer, y borrando sólo la fila que se leyó quedaría la otra huérfana.
    Lo que esté adjunto a algo no se toca."""
    for url in sorted(set(urls)):
        for name in frappe.get_all("File", filters={"file_url": url, "attached_to_doctype": ["in", ["", None]]}, pluck="name"):
            frappe.delete_doc("File", name, ignore_permissions=True, force=1)


def _archivo_del_sitio(url):
    """Sólo archivos que ya están en el sitio: se busca el File por file_url y nunca se abre una
    ruta suelta del servidor. Además se comprueba que el usuario pueda leer ese File."""
    if not isinstance(url, str) or ".." in url or not url.startswith(CARPETAS_DEL_SITIO):
        frappe.throw(_("Ruta de archivo no permitida: {0}").format(url))
    name = frappe.db.get_value("File", {"file_url": url}, "name")
    if not name:
        frappe.throw(_("El archivo {0} no está registrado en el sitio.").format(url))
    archivo = frappe.get_doc("File", name)
    archivo.check_permission("read")
    return archivo


def _a_bytes(contenido):
    """File.get_content() decodifica a str lo que es texto (el XML) y deja en bytes lo binario."""
    return contenido.encode("utf-8") if isinstance(contenido, str) else contenido


def _desempacar(nombre, contenido):
    """Devuelve [(nombre_xml, bytes_xml, bytes_pdf_o_None)]. Un ZIP puede traer pares XML/PDF por
    nombre base (F77.xml + F77.pdf); lo que no sea .xml o .pdf se ignora."""
    if not nombre.lower().endswith(".zip"):
        return [(nombre, contenido, None)]
    miembros = _leer_zip(contenido)
    pdfs = {os.path.splitext(n)[0].lower(): b for n, b in miembros.items() if n.lower().endswith(".pdf")}
    return [(n, b, pdfs.get(os.path.splitext(n)[0].lower()))
            for n, b in miembros.items() if n.lower().endswith(".xml")]


def _leer_zip(contenido):
    """{nombre_base: bytes} de los .xml y .pdf del ZIP, con topes contra el 'zip bomb'."""
    miembros, leidos = {}, 0
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        entradas = [i for i in z.infolist() if not i.is_dir()]
        if len(entradas) > MAX_ENTRADAS_ZIP:
            frappe.throw(_("El ZIP trae {0} archivos y el máximo son {1}.").format(len(entradas), MAX_ENTRADAS_ZIP))
        if sum(i.file_size for i in entradas) > MAX_BYTES_ZIP:
            frappe.throw(_("El ZIP dice ocupar más de {0} MB descomprimido.").format(MAX_BYTES_ZIP // 1048576))
        for info in entradas:
            if not _ruta_segura(info.filename) or not info.filename.lower().endswith((".xml", ".pdf")):
                continue
            with z.open(info) as f:
                # Un byte de más para cazar al ZIP que miente sobre el tamaño de sus archivos.
                datos = f.read(MAX_BYTES_MIEMBRO + 1)
            if len(datos) > MAX_BYTES_MIEMBRO:
                frappe.throw(_("El archivo {0} del ZIP pasa de {1} MB.").format(info.filename, MAX_BYTES_MIEMBRO // 1048576))
            leidos += len(datos)
            if leidos > MAX_BYTES_ZIP:
                frappe.throw(_("El ZIP pasa de {0} MB descomprimido.").format(MAX_BYTES_ZIP // 1048576))
            miembros[posixpath.basename(info.filename)] = datos
    return miembros


def _ruta_segura(ruta):
    """Se ignoran los miembros con ruta absoluta o con '..': aunque sólo se use el nombre base,
    un ZIP así viene mal intencionado y no hay por qué leerlo."""
    partes = ruta.replace("\\", "/").split("/")
    return not ruta.startswith("/") and ".." not in partes and bool(partes[-1])


def _procesar_uno(nombre, xml_bytes, pdf_bytes, origen, resultado):
    try:
        cfdi = procesar_xml(xml_bytes, origen, nombre, pdf_bytes)
    except Exception as e:
        _anotar_error(resultado, nombre, e)
        return
    if cfdi.flags.duplicado:
        resultado["duplicados"].append(cfdi.name)
        return
    resultado["nuevos"].append(cfdi.name)
    if cfdi.estado == "Ajeno":
        resultado["ajenos"].append(cfdi.name)
        return
    if cfdi.tipo_comprobante in ("I", "E"):
        try:
            resultado["facturas"].append(crear_factura_desde_cfdi(cfdi.name))
        except Exception as e:      # el CFDI queda registrado; la factura se reintenta desde la bandeja
            cfdi.db_set({"estado": "Error", "error": str(e)[:1000]})
            _anotar_error(resultado, nombre, e)


def _anotar_error(resultado, archivo, error):
    # frappe.throw deja el mensaje en la cola y el navegador lo mostraría como un diálogo suelto por
    # cada archivo; aquí el error va en el resumen de la carga y nada más.
    frappe.clear_last_message()
    resultado["errores"].append({"archivo": archivo, "error": str(error)})


@frappe.whitelist()
def crear_factura(cfdi):
    """Botón 'Crear factura de compra' del CFDI Recibido (también sirve para reintentar un error)."""
    frappe.has_permission("Purchase Invoice", "create", throw=True)
    doc = frappe.get_doc("CFDI Recibido", cfdi)
    doc.check_permission("read")
    if doc.estado == "Error":
        doc.db_set({"estado": "Nuevo", "error": ""})
    return crear_factura_desde_cfdi(cfdi)
