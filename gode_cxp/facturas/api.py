"""Puntos de entrada desde la interfaz: subir XML/ZIP y crear la factura de un CFDI."""
import io
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
    frappe.has_permission("Purchase Invoice", "create", throw=True)
    if origen not in ORIGENES:
        frappe.throw(_("Origen no válido: {0}").format(origen))
    if isinstance(file_urls, str):
        file_urls = frappe.parse_json(file_urls)
    if not isinstance(file_urls, (list, tuple)):
        frappe.throw(_("Se esperaba una lista de archivos."))

    resultado = {"nuevos": [], "duplicados": [], "ajenos": [], "errores": [], "facturas": [], "con_error": []}
    temporales, indice = [], 0
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
        for etiqueta, xml_nombre, xml_bytes, pdf_bytes in partes:
            _procesar_uno(indice, etiqueta, xml_nombre, xml_bytes, pdf_bytes, origen, resultado)
            indice += 1
    _borrar_temporales(temporales)
    return resultado


def _filtros_de_la_subida(url=None):
    """Lo único que esta API acepta y borra: un File privado, sin adjuntar y del usuario que llama.
    Sin el dueño, cualquier File público del sitio (File.has_permission deja leerlos a todos) pasaría
    por 'archivo que acabo de subir' y además se borraría al final."""
    filtros = {"owner": frappe.session.user, "is_private": 1, "is_folder": 0,
               "attached_to_doctype": ["in", ["", None]], "attached_to_name": ["in", ["", None]]}
    if url is not None:
        filtros["file_url"] = url
    return filtros


def _archivo_del_sitio(url):
    """Sólo archivos que el propio usuario acaba de subir: se busca el File por file_url y nunca se
    abre una ruta suelta del servidor."""
    if not isinstance(url, str) or ".." in url or not url.startswith(CARPETAS_DEL_SITIO):
        frappe.throw(_("Ruta de archivo no permitida: {0}").format(url))
    name = frappe.db.get_value("File", _filtros_de_la_subida(url), "name")
    if not name:
        frappe.throw(_("Solo se pueden procesar archivos que tú acabas de subir: {0}").format(url))
    archivo = frappe.get_doc("File", name)
    archivo.check_permission("read")
    return archivo


def _borrar_temporales(urls):
    """El archivo de la subida es temporal: el XML (y el PDF) definitivos ya quedaron adjuntos al
    CFDI Recibido desde recepcion.procesar_xml. Se borra al final y buscando por file_url porque
    Frappe le da el MISMO file_url a dos subidas con el mismo contenido
    (File.validate_duplicate_entry): borrando dentro del bucle, el segundo archivo del lote se
    quedaría sin nada que leer, y borrando sólo la fila que se leyó quedaría la otra huérfana.
    Se borra con los mismos filtros con que se aceptó: nada que sea de otro o esté adjunto a algo."""
    for url in sorted(set(urls)):
        for name in frappe.get_all("File", filters=_filtros_de_la_subida(url), pluck="name"):
            frappe.delete_doc("File", name, ignore_permissions=True, force=1)


def _a_bytes(contenido):
    """File.get_content() decodifica a str lo que es texto (el XML) y deja en bytes lo binario."""
    return contenido.encode("utf-8") if isinstance(contenido, str) else contenido


def _desempacar(nombre, contenido):
    """Devuelve [(etiqueta, nombre_xml, bytes_xml, bytes_pdf_o_None)]. La etiqueta es la ruta dentro
    del ZIP (para el resumen de errores) y el nombre es el del archivo (el File se llama así).
    El PDF se empareja por carpeta + nombre sin extensión: 'A/F1.pdf' es el de 'A/F1.xml' y no el de
    'B/F1.xml'; dos carpetas pueden traer archivos con el mismo nombre y son dos CFDI distintos."""
    if not nombre.lower().endswith(".zip"):
        return [(nombre, nombre, contenido, None)]
    miembros = _leer_zip(contenido)
    pdfs = {_clave_de_pareja(r): b for r, b in miembros.items() if r.lower().endswith(".pdf")}
    return [(r, posixpath.basename(r), b, pdfs.get(_clave_de_pareja(r)))
            for r, b in miembros.items() if r.lower().endswith(".xml")]


def _clave_de_pareja(ruta):
    return posixpath.splitext(ruta)[0].lower()


def _leer_zip(contenido):
    """{ruta_dentro_del_zip: bytes} de los .xml y .pdf del ZIP, con topes contra el 'zip bomb'."""
    miembros, leidos = {}, 0
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        entradas = [i for i in z.infolist() if not i.is_dir()]
        if len(entradas) > MAX_ENTRADAS_ZIP:
            frappe.throw(_("El ZIP trae {0} archivos y el máximo son {1}.").format(len(entradas), MAX_ENTRADAS_ZIP))
        if sum(i.file_size for i in entradas) > MAX_BYTES_ZIP:
            frappe.throw(_("El ZIP dice ocupar más de {0} MB descomprimido.").format(MAX_BYTES_ZIP // 1048576))
        for info in entradas:
            ruta = _ruta_segura(info.filename)
            if not ruta or not ruta.lower().endswith((".xml", ".pdf")):
                continue
            with z.open(info) as f:
                # Un byte de más para cazar al ZIP que miente sobre el tamaño de sus archivos.
                datos = f.read(MAX_BYTES_MIEMBRO + 1)
            if len(datos) > MAX_BYTES_MIEMBRO:
                frappe.throw(_("El archivo {0} del ZIP pasa de {1} MB.").format(info.filename, MAX_BYTES_MIEMBRO // 1048576))
            leidos += len(datos)
            if leidos > MAX_BYTES_ZIP:
                frappe.throw(_("El ZIP pasa de {0} MB descomprimido.").format(MAX_BYTES_ZIP // 1048576))
            miembros[ruta] = datos
    return miembros


def _ruta_segura(ruta):
    """Devuelve la ruta relativa del miembro, o None si el ZIP viene mal intencionado: ruta absoluta
    o con '..'. No se aplana: la carpeta forma parte de la identidad del archivo."""
    partes = [p for p in ruta.replace("\\", "/").split("/") if p not in ("", ".")]
    if ruta.startswith("/") or ".." in partes or not partes:
        return None
    return "/".join(partes)


def _procesar_uno(indice, etiqueta, nombre, xml_bytes, pdf_bytes, origen, resultado):
    """Cada archivo del lote va en su propio punto de retorno: si revienta a media faena, se deshace
    lo que dejó a medias y el lote sigue con el siguiente."""
    punto = f"cxp_{indice}"
    frappe.db.savepoint(punto)
    try:
        cfdi = procesar_xml(xml_bytes, origen, nombre, pdf_bytes)
    except Exception as e:
        frappe.db.rollback(save_point=punto)
        _anotar_error(resultado, etiqueta, e)
        return
    if cfdi.flags.duplicado:
        resultado["duplicados"].append(cfdi.name)
        return
    resultado["nuevos"].append(cfdi.name)
    if cfdi.estado == "Ajeno":
        resultado["ajenos"].append(cfdi.name)
        return
    if cfdi.tipo_comprobante not in ("I", "E"):
        return

    # Segundo punto de retorno: si la factura falla después de insertarse, se deshace la factura a
    # medio hacer pero el CFDI se queda registrado en estado "Error" para reintentarlo desde la bandeja.
    punto_factura = f"{punto}_factura"
    frappe.db.savepoint(punto_factura)
    try:
        factura = crear_factura_desde_cfdi(cfdi.name)
    except Exception as e:
        frappe.db.rollback(save_point=punto_factura)
        cfdi.db_set({"estado": "Error", "error": str(e)[:1000]})
        _anotar_error(resultado, etiqueta, e)
        return
    resultado["facturas"].append(factura)
    if frappe.db.get_value("Purchase Invoice", factura, "estado_revision") == "Error de lectura":
        # La factura se creó pero su total no cuadra con el del XML: hay que revisarla a mano.
        resultado["con_error"].append(factura)


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
