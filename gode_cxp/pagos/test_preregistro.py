"""Pre-registro de cuentas de proveedor en BancaNet: filas de la plantilla, respuesta del banco y
candado del lote.

El banco exige dar de alta la cuenta antes de poder pagarle al proveedor. La app NO genera el
archivo de alta (sólo hay una muestra, de cuentas Banamex de cheques de personas físicas): produce
las filas para pegar en la plantilla con macros del banco y lee la respuesta.
"""
import io
import json
import os
import re
import unittest
from datetime import date

import frappe
import openpyxl
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import cuenta_verificada, factura_aprobada, usuario
from gode_cxp.pagos import api, preregistro
from gode_cxp.pagos.lotes import crear_lotes
from gode_cxp.setup.produccion import configurar_pagos

CLABE_12 = "072180007090045065"          # Banorte: interbancario, naturaleza 12
OTRA_CLABE_12 = "072180007090045997"     # otra CLABE válida del mismo banco
CLABE_06 = "002180012345678906"          # Banamex: naturaleza 06 (sucursal + cuenta)
CLABE_DESCONOCIDA = "998180007090045068"  # clave 998: no está en el catálogo del banco
SUCURSAL, CUENTA = "7005", "7479513"
CUENTA_20_06 = "0" * 9 + SUCURSAL + CUENTA    # cómo viaja una cuenta Banamex en el archivo del banco
CUENTA_20_12 = "00" + CLABE_12                # y cómo viaja una CLABE interbancaria
BENEFICIARIO_MORAL = "AVICOLA,DEL CARMEN SA DE CV/"
BENEFICIARIO_FISICA = "BRUNO RICARDO,HERNANDEZ/SILVA"
TESORERIA, REVISOR = "prueba.tesoreria@cxp.local", "prueba.revisor@cxp.local"
FECHA = date(2026, 9, 17)
# La muestra REAL de una respuesta de alta vive fuera del repo (trae datos personales de empleados):
# bench-pruebas.sh monta /home/sergio/service-env/tef-muestras aquí, de sólo lectura.
MUESTRA_REAL = "/home/frappe/tef-muestras/abcMasivoTERINT170920260001815117770000000000020001.txt"


def registro(banco, tipo, cuenta20, beneficiario, codigo, mensaje):
    """Un registro de detalle (325) del archivo de respuesta, armado con las posiciones A MANO y no
    con las constantes del módulo: si el lector se equivoca de posición, la prueba tiene que notarlo.

    'A' (1) + banco (2-5) + tipo de cuenta (6-7) + un número fijo del contrato (8-19) +
    cuenta a 20 (20-39) + beneficiario (40-94) + … + código (266-269) + mensaje (270-325).
    """
    linea = "A" + banco + tipo + "0" * 12 + cuenta20 + beneficiario.ljust(55)
    assert len(linea) == 94, len(linea)
    linea = linea.ljust(265) + codigo + mensaje.ljust(56)
    assert len(linea) == 325, len(linea)
    return linea


def alta_06(codigo="0000", mensaje="ALTA APLICADA", beneficiario=BENEFICIARIO_FISICA):
    """El registro con el que el banco contesta por la cuenta Banamex de la persona física."""
    return registro("0002", "01", CUENTA_20_06, beneficiario, codigo, mensaje)


def alta_12(codigo="0000", mensaje="ALTA APLICADA", beneficiario=BENEFICIARIO_MORAL, cuenta20=CUENTA_20_12):
    """Y el registro de la cuenta interbancaria de la persona moral."""
    return registro("0072", "40", cuenta20, beneficiario, codigo, mensaje)


def archivo_respuesta(registros, encabezado=True, salto="\r\n"):
    """Encabezado de 115 + los registros, latin-1, CRLF ENTRE líneas y sin CRLF al final (así llega
    el archivo real del banco).

    `encabezado=False` y `salto` sirven para probar lo que el lector tiene que rechazar (un archivo
    que no es una respuesta del banco) y lo que tiene que tolerar (cualquier fin de línea)."""
    lineas = list(registros)
    if encabezado:
        cabecera = ("0001" + "17/09/2026" + "05:26" + "000181511777").ljust(115)
        assert len(cabecera) == 115
        lineas.insert(0, cabecera)
    return salto.join(lineas).encode("latin-1")


def subir(contenido, nombre="abcMasivo-prueba.txt", privado=1):
    """Deja el archivo como File suelto y devuelve su file_url (es lo que sube Tesorería).

    El File queda a nombre del usuario de la sesión, y eso importa: el lector exige permiso de
    lectura sobre el archivo y en Frappe un File privado suelto sólo lo lee su dueño."""
    return frappe.get_doc({"doctype": "File", "file_name": nombre, "content": contenido,
                           "is_private": privado}).insert(ignore_permissions=True).file_url


def solo_ve(caso, doctype, valor, correo=TESORERIA):
    """Le deja a `correo` un único documento permitido de `doctype`, o sea le niega todos los demás.

    Misma técnica que test_api.py (allá sobre el lote de pago): es la forma real de ejercitar las
    User Permissions por empresa sin dar de alta otro catálogo en el sitio de pruebas. `ignore_links`
    porque a una User Permission no le importa que el valor exista —sólo guarda la lista de valores
    permitidos— y aquí lo que se quiere es justamente que NO sea el documento de la prueba."""
    up = frappe.get_doc({"doctype": "User Permission", "user": correo, "allow": doctype,
                         "for_value": valor})
    up.flags.ignore_links = True
    up.insert(ignore_permissions=True)
    # LIFO: primero se borra la User Permission y después se limpia la caché del usuario.
    caso.addCleanup(frappe.clear_cache, user=correo)
    caso.addCleanup(frappe.delete_doc, "User Permission", up.name, force=1, ignore_permissions=True)
    frappe.clear_cache(user=correo)


class TestPreregistro(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()
        banco = frappe.db.get_value("Account", {"company": pruebas_comun.EMPRESA, "account_type": "Bank", "is_group": 0}, "name")
        configurar_pagos(pruebas_comun.EMPRESA, "000181511777", "7007", "8382129",
                         "GASTRONOMICA DE ESPECIALIDADES GODE", "pago gode", banco, dry_run=False)

    def setUp(self):
        frappe.set_user("Administrator")
        pruebas_comun.limpiar()
        usuario(TESORERIA, "CxP Tesoreria"); usuario(REVISOR, "CxP Revisor")
        # Proveedor MORAL (AVICOLA DEL CARMEN SA DE CV) con cuenta interbancaria, y proveedor FÍSICO
        # (BRUNO RICARDO HERNANDEZ SILVA) con cuenta Banamex: las dos formas de fila de la plantilla.
        self.moral = factura_aprobada(ejemplos.INGRESO_40)
        self.fisica = factura_aprobada(ejemplos.INGRESO_33_RETENCIONES)
        self.cta_12 = cuenta_verificada(self.moral.supplier, CLABE_12, registrada=False)
        self.cta_06 = cuenta_verificada(self.fisica.supplier, CLABE_06, banco="Banamex",
                                        sucursal=SUCURSAL, cuenta=CUENTA, registrada=False)

    def tearDown(self):
        frappe.set_user("Administrator")

    def _enviadas(self, *cuentas):
        """Deja las cuentas esperando respuesta del banco, que es lo que hace la descarga del
        pre-registro. Se escribe con set_value porque el estado del alta no se cambia con un save()."""
        for name in cuentas:
            frappe.db.set_value("Bank Account", name, {"estado_preregistro": "Enviada al banco",
                                                       "preregistro_enviado_el": now_datetime()})

    def _cuenta_gemela(self, clabe, nombre="Cuenta gemela"):
        """Otra Bank Account de proveedor con la MISMA CLABE. El campo no es único (y en la vida real
        pasa: la misma cuenta capturada dos veces), y el banco contesta un número de cuenta, no un
        name, así que el cruce tiene que poder toparse con dos candidatas."""
        doc = frappe.get_doc({"doctype": "Bank Account", "account_name": nombre, "bank": "Banorte",
                              "party_type": "Supplier", "party": self.moral.supplier, "clabe": clabe})
        doc.insert(ignore_permissions=True)
        return doc.name

    # --- las filas para la plantilla del banco -----------------------------------------------------

    def test_fila_de_una_cuenta_interbancaria_de_persona_moral(self):
        (fila,) = preregistro.filas_para_plantilla([self.cta_12])
        self.assertEqual(fila, {
            "MOVIMIENTO": "ALTA",
            "BANCO": "BANORTE",
            "SUCURSAL": "",
            "TIPO DE CUENTA": "CLABE INTERBANCARIA",
            "NUMERO DE CUENTA": CLABE_12,
            "TIPO DE PERSONA": "PERSONA MORAL",
            "PERIODO": "DIARIO",
            "IMPORTE MAXIMO": 500000,
            "BENEFICIARIO": BENEFICIARIO_MORAL,
            "ALIAS": "AVICOLA DEL CARMEN S",
            "RFC": "AVI900101AB1",
            "CELULAR": "",
            "EMAIL": "",
        })

    def test_fila_de_una_cuenta_banamex_de_persona_fisica(self):
        (fila,) = preregistro.filas_para_plantilla([self.cta_06])
        self.assertEqual(fila, {
            "MOVIMIENTO": "ALTA",
            "BANCO": "BANAMEX",
            "SUCURSAL": SUCURSAL,
            "TIPO DE CUENTA": "CHEQUES",
            "NUMERO DE CUENTA": CUENTA,
            "TIPO DE PERSONA": "PERSONA FISICA",
            "PERIODO": "DIARIO",
            "IMPORTE MAXIMO": 500000,
            "BENEFICIARIO": BENEFICIARIO_FISICA,
            "ALIAS": "BRUNO RICARDO HERNAN",
            "RFC": "HESB850101AB1",
            "CELULAR": "",
            "EMAIL": "",
        })

    def test_el_importe_maximo_de_la_cuenta_le_gana_al_de_la_configuracion(self):
        frappe.db.set_value("Bank Account", self.cta_12, "importe_maximo_banco", 12345)
        (fila,) = preregistro.filas_para_plantilla([self.cta_12])
        self.assertEqual(fila["IMPORTE MAXIMO"], 12345)

    def test_el_banco_sale_de_los_tres_primeros_digitos_de_la_clabe(self):
        """Y si la clave no está en el catálogo, va el código de 3 dígitos: el banco rechaza la fila
        y se ve por qué, en vez de mandar un hueco."""
        self.assertEqual(preregistro.banco_de_clabe(CLABE_06), "BANAMEX")
        self.assertEqual(preregistro.banco_de_clabe(CLABE_12), "BANORTE")
        self.assertEqual(preregistro.banco_de_clabe("012180007090045067"), "BBVA")
        self.assertEqual(preregistro.banco_de_clabe(CLABE_DESCONOCIDA), "998")
        self.assertEqual(preregistro.banco_de_clabe(""), "")

    def test_solo_salen_las_cuentas_verificadas_que_faltan_por_registrar(self):
        self.assertEqual({f["NUMERO DE CUENTA"] for f in preregistro.filas_para_plantilla()},
                         {CLABE_12, CUENTA})
        frappe.db.set_value("Bank Account", self.cta_12, "estado_preregistro", "Registrada")
        self.assertEqual({f["NUMERO DE CUENTA"] for f in preregistro.filas_para_plantilla()}, {CUENTA})
        # Una cuenta que Tesorería no ha verificado no se manda al banco ni pidiéndola por su nombre.
        frappe.db.set_value("Bank Account", self.cta_06, "verificada", 0)
        self.assertEqual(preregistro.filas_para_plantilla(), [])
        with self.assertRaises(frappe.ValidationError):
            preregistro.filas_para_plantilla([self.cta_06])

    def test_una_cuenta_cuyo_proveedor_ya_no_existe_lo_dice_en_espanol(self):
        """El Link protege el borrado, pero una base tocada a mano deja la cuenta apuntando a un
        proveedor que no está: el mensaje tiene que decirlo, no reventar con AttributeError."""
        antes = frappe.db.get_value("Bank Account", self.cta_12, "party")
        frappe.db.set_value("Bank Account", self.cta_12, "party", "PROVEEDOR QUE NO EXISTE")
        # Se repone el proveedor: limpiar() encuentra las cuentas de prueba POR su proveedor, y una
        # cuenta huérfana sobreviviría y choparía por nombre con la que cree la prueba siguiente.
        self.addCleanup(frappe.db.set_value, "Bank Account", self.cta_12, "party", antes)
        with self.assertRaises(frappe.ValidationError) as ctx:
            preregistro.filas_para_plantilla([self.cta_12])
        self.assertIn("PROVEEDOR QUE NO EXISTE", str(ctx.exception))

    # --- la descarga del XLSX ----------------------------------------------------------------------

    def test_descargar_arma_el_xlsx_y_marca_las_cuentas_enviadas(self):
        frappe.set_user(TESORERIA)
        r = api.descargar_preregistro()
        self.assertEqual(r["cuentas"], 2)
        self.assertTrue(re.fullmatch(r"preregistro-\d{8}-\d{4}\.xlsx", r["nombre"]), r["nombre"])
        contenido = frappe.get_doc("File", {"file_url": r["file_url"]}).get_content()
        libro = openpyxl.load_workbook(io.BytesIO(contenido))
        self.assertEqual(libro.sheetnames, ["LOIncorOP"])
        hoja = libro["LOIncorOP"]
        self.assertEqual([c.value for c in hoja[1]], list(preregistro.COLUMNAS))
        self.assertEqual(hoja.max_row, 3)          # encabezados + 2 cuentas
        self.assertEqual(hoja.max_column, len(preregistro.COLUMNAS))
        for cuenta in (self.cta_12, self.cta_06):
            datos = frappe.db.get_value("Bank Account", cuenta,
                                        ["estado_preregistro", "preregistro_enviado_el"], as_dict=True)
            self.assertEqual(datos.estado_preregistro, "Enviada al banco")
            self.assertTrue(datos.preregistro_enviado_el)
        # Ya enviadas, no hay nada que descargar otra vez.
        with self.assertRaises(frappe.ValidationError):
            api.descargar_preregistro()

    def test_descargar_es_de_tesoreria(self):
        frappe.set_user(REVISOR)
        with self.assertRaises(frappe.PermissionError):
            api.descargar_preregistro()

    def test_reenviar_si_se_puede_menos_lo_que_el_banco_ya_registro(self):
        """Volver a mandar al banco una cuenta que rechazó, o una que se quedó sin respuesta, es lo
        normal. Volver a mandar una que YA está registrada, no: la regresaría a 'Enviada al banco',
        o sea le quitaría el permiso de cobrar hasta la próxima respuesta, sin que nadie lo pidiera."""
        frappe.db.set_value("Bank Account", self.cta_12, "estado_preregistro", "Registrada")
        frappe.db.set_value("Bank Account", self.cta_06, "estado_preregistro", "Rechazada")
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.ValidationError) as ctx:
            api.descargar_preregistro(json.dumps([self.cta_12, self.cta_06]))
        self.assertIn(self.cta_12, str(ctx.exception))
        self.assertNotIn(self.cta_06, str(ctx.exception))
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_12, "estado_preregistro"), "Registrada")
        # La rechazada sí se vuelve a mandar…
        self.assertEqual(api.descargar_preregistro(json.dumps([self.cta_06]))["cuentas"], 1)
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_06, "estado_preregistro"),
                         "Enviada al banco")
        # …y también una que ya está esperando respuesta (el banco no contestó, se manda otra vez).
        self.assertEqual(api.descargar_preregistro(json.dumps([self.cta_06]))["cuentas"], 1)

    def test_la_lista_de_cuentas_tiene_que_ser_de_nombres(self):
        """Lo que manda el escritorio es un JSON con una lista de names. Cualquier otra cosa (un
        número, un dict, una lista de dicts) tiene que dar un mensaje en español y no un TypeError
        adentro del filtro de la consulta."""
        frappe.set_user(TESORERIA)
        for malo in (5, {"cuenta": self.cta_12}, [1, 2], [{"name": self.cta_12}], json.dumps([1, 2])):
            with self.assertRaises(frappe.ValidationError):
                api.marcar_registrada(malo)
        # Un solo nombre en texto plano sí se acepta: es lo que llega de una llamada de una sola fila.
        self.assertEqual(api.marcar_registrada(self.cta_12)["registradas"], 1)

    # --- la respuesta del banco -------------------------------------------------------------------

    def test_leer_la_respuesta_del_banco(self):
        datos = archivo_respuesta([alta_06(), alta_12("0015", "CUENTA INEXISTENTE")])
        cheques, clabe = preregistro.leer_respuesta_preregistro(datos)
        self.assertEqual(cheques, {"banco": "0002", "tipo_cuenta": "01", "cuenta": CUENTA_20_06,
                                   "sucursal": SUCURSAL, "cuenta_banamex": CUENTA, "clabe": None,
                                   "beneficiario": BENEFICIARIO_FISICA,
                                   "codigo": "0000", "mensaje": "ALTA APLICADA"})
        self.assertEqual(clabe, {"banco": "0072", "tipo_cuenta": "40", "cuenta": CUENTA_20_12,
                                 "sucursal": None, "cuenta_banamex": None, "clabe": CLABE_12,
                                 "beneficiario": BENEFICIARIO_MORAL,
                                 "codigo": "0015", "mensaje": "CUENTA INEXISTENTE"})

    def test_un_registro_corto_no_revienta(self):
        """Si la línea no llega a la posición del código (o del mensaje), esos dos quedan vacíos en
        lugar de tirar la carga entera."""
        (leido,) = preregistro.leer_respuesta_preregistro(archivo_respuesta([alta_06()[:150]]))
        self.assertEqual((leido["codigo"], leido["mensaje"]), ("", ""))
        self.assertEqual(leido["cuenta_banamex"], CUENTA)

    def test_el_lector_tolera_cualquier_fin_de_linea(self):
        """El archivo del banco viene con CRLF, pero pasa por Windows, por el correo y por un editor:
        \\n y \\r suelto tienen que leerse igual."""
        for salto in ("\r\n", "\n", "\r"):
            datos = archivo_respuesta([alta_06(), alta_12()], salto=salto)
            self.assertEqual(len(preregistro.leer_respuesta_preregistro(datos)), 2, salto.encode())

    def test_un_archivo_sin_el_encabezado_del_banco_no_se_lee(self):
        """Sin el encabezado no es una respuesta de BancaNet: puede ser cualquier TXT, y aplicarlo a
        medias movería estados de cuentas por coincidencia."""
        with self.assertRaises(frappe.ValidationError) as ctx:
            preregistro.leer_respuesta_preregistro(archivo_respuesta([alta_06()], encabezado=False))
        self.assertIn("encabezado", str(ctx.exception))

    def test_un_archivo_sin_ningun_alta_no_se_lee(self):
        with self.assertRaises(frappe.ValidationError) as ctx:
            preregistro.leer_respuesta_preregistro(archivo_respuesta([]))
        self.assertIn("alta", str(ctx.exception))

    def test_aplicar_registra_rechaza_y_es_idempotente(self):
        datos = archivo_respuesta([alta_06(), alta_12("0015", "CUENTA INEXISTENTE"),
                                   alta_12(beneficiario="QUIEN,SABE/", cuenta20="00" + OTRA_CLABE_12)])
        self._enviadas(self.cta_06, self.cta_12)
        frappe.set_user(TESORERIA)
        file_url = subir(datos)
        r = api.aplicar_respuesta_preregistro(file_url)
        self.assertEqual((r["registradas"], r["rechazadas"]), (1, 1))
        self.assertEqual(r["sin_coincidencia"],
                         [{"cuenta": OTRA_CLABE_12[-4:], "motivo": "no hay ninguna cuenta con ese número"}])
        self.assertEqual(tuple(frappe.db.get_value("Bank Account", self.cta_06,
                                                   ["estado_preregistro", "preregistro_respuesta"])),
                         ("Registrada", "0000 ALTA APLICADA"))
        self.assertEqual(tuple(frappe.db.get_value("Bank Account", self.cta_12,
                                                   ["estado_preregistro", "preregistro_respuesta"])),
                         ("Rechazada", "0015 CUENTA INEXISTENTE"))
        # Cargar dos veces el mismo archivo no cambia nada: las dos cuentas ya contestadas dejaron de
        # esperar respuesta, así que el segundo pase no toca ninguna y lo dice.
        otra = api.aplicar_respuesta_preregistro(file_url)
        self.assertEqual((otra["registradas"], otra["rechazadas"]), (0, 0))
        self.assertEqual([s["motivo"] for s in otra["sin_coincidencia"]],
                         ["la cuenta no está en espera de respuesta",
                          "la cuenta no está en espera de respuesta",
                          "no hay ninguna cuenta con ese número"])
        self.assertEqual(tuple(frappe.db.get_value("Bank Account", self.cta_06,
                                                   ["estado_preregistro", "preregistro_respuesta"])),
                         ("Registrada", "0000 ALTA APLICADA"))

    def test_sin_coincidencia_no_devuelve_el_numero_de_cuenta_completo(self):
        """Lo que vuelve a la pantalla (y de ahí a un correo o a una captura) lleva sólo los últimos
        cuatro dígitos: es un número de cuenta de un proveedor, no un dato de la app."""
        datos = archivo_respuesta([alta_12(cuenta20="00" + OTRA_CLABE_12)])
        frappe.set_user(TESORERIA)
        (sin,) = api.aplicar_respuesta_preregistro(subir(datos))["sin_coincidencia"]
        self.assertEqual(sin["cuenta"], OTRA_CLABE_12[-4:])
        self.assertNotIn(OTRA_CLABE_12, json.dumps(sin))

    def test_solo_se_toca_la_cuenta_que_espera_respuesta(self):
        """El banco contesta a una solicitud: si la cuenta no está 'Enviada al banco' (nunca se mandó,
        o ya se contestó, o cambió y hay que volver a mandarla), este archivo no es sobre ella."""
        datos = archivo_respuesta([alta_06()])          # la cuenta sigue en 'Sin registrar'
        frappe.set_user(TESORERIA)
        r = api.aplicar_respuesta_preregistro(subir(datos))
        self.assertEqual((r["registradas"], r["rechazadas"]), (0, 0))
        self.assertEqual(r["sin_coincidencia"],
                         [{"cuenta": CUENTA[-4:], "motivo": "la cuenta no está en espera de respuesta"}])
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_06, "estado_preregistro"),
                         "Sin registrar")

    def test_dos_cuentas_con_el_mismo_numero_no_se_tocan(self):
        """El banco contesta un número de cuenta, no un name: con dos Bank Account que lo tienen, la
        app no puede adivinar a cuál le dijo sí. Antes se tomaba la primera que devolvía la consulta."""
        self._cuenta_gemela(CLABE_12)
        self._enviadas(self.cta_12)
        frappe.set_user(TESORERIA)
        r = api.aplicar_respuesta_preregistro(subir(archivo_respuesta([alta_12()])))
        self.assertEqual((r["registradas"], r["rechazadas"]), (0, 0))
        self.assertEqual(r["sin_coincidencia"],
                         [{"cuenta": CLABE_12[-4:], "motivo": "cuenta duplicada"}])
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_12, "estado_preregistro"),
                         "Enviada al banco")

    def test_una_cuenta_deshabilitada_no_estorba_ni_se_registra(self):
        """Una cuenta dada de baja en la app no se manda al banco ni se registra, y sobre todo no
        cuenta como duplicado de la que sí está viva."""
        gemela = self._cuenta_gemela(CLABE_12)
        frappe.db.set_value("Bank Account", gemela, "disabled", 1)
        self._enviadas(self.cta_12, gemela)
        frappe.set_user(TESORERIA)
        r = api.aplicar_respuesta_preregistro(subir(archivo_respuesta([alta_12()])))
        self.assertEqual((r["registradas"], r["rechazadas"]), (1, 0))
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_12, "estado_preregistro"),
                         "Registrada")
        self.assertEqual(frappe.db.get_value("Bank Account", gemela, "estado_preregistro"),
                         "Enviada al banco")

    def test_un_archivo_viejo_no_registra_una_cuenta_cuyo_beneficiario_cambio(self):
        """El caso real: el banco dijo sí a un beneficiario, después Tesorería corrigió el nombre y
        volvió a mandar la cuenta. Si se carga la respuesta VIEJA, el sí no es de esta cuenta."""
        viejo = archivo_respuesta([alta_06(beneficiario=BENEFICIARIO_FISICA)])
        doc = frappe.get_doc("Bank Account", self.cta_06)
        doc.nombre_tef = "BRUNO,HERNANDEZ/SILVA"
        doc.save()                        # cambiar el beneficiario deja la cuenta en 'Sin registrar'
        self._enviadas(self.cta_06)       # y Tesorería la vuelve a mandar al banco
        frappe.set_user(TESORERIA)
        r = api.aplicar_respuesta_preregistro(subir(viejo))
        self.assertEqual((r["registradas"], r["rechazadas"]), (0, 0))
        self.assertEqual(r["sin_coincidencia"],
                         [{"cuenta": CUENTA[-4:], "motivo": "el beneficiario del banco no coincide"}])
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_06, "estado_preregistro"),
                         "Enviada al banco")

    def test_el_beneficiario_se_compara_sin_espacios_de_sobra(self):
        """El banco devuelve el beneficiario dentro de un campo de 55 y con la separación que se le
        ocurra: la comparación es sobre el texto normalizado, no carácter por carácter."""
        self._enviadas(self.cta_12)
        frappe.set_user(TESORERIA)
        datos = archivo_respuesta([alta_12(beneficiario="  avicola,del   carmen sa de cv/ ")])
        r = api.aplicar_respuesta_preregistro(subir(datos))
        self.assertEqual((r["registradas"], r["sin_coincidencia"]), (1, []))

    def test_el_mensaje_del_banco_se_lee_en_latin_1(self):
        """El archivo viene en latin-1 y los mensajes del banco traen Ñ: se lee en BYTES y se
        decodifica a propósito. Con `File.get_content()` (que decide por su cuenta si es texto) la Ñ
        se convierte en otro carácter o revienta la carga."""
        mensaje = "CUENTA DE OTRA COMPAÑIA"
        self._enviadas(self.cta_06)
        frappe.set_user(TESORERIA)
        r = api.aplicar_respuesta_preregistro(subir(archivo_respuesta([alta_06("0015", mensaje)])))
        self.assertEqual(r["rechazadas"], 1)
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_06, "preregistro_respuesta"),
                         f"0015 {mensaje}")

    def test_la_respuesta_tiene_que_ser_un_archivo_privado(self):
        """El archivo trae nombres de proveedores y sus cuentas: si se subió como público, la app no
        lo aplica y lo dice, en vez de dejarlo servido en /files para cualquiera."""
        frappe.set_user(TESORERIA)
        file_url = subir(archivo_respuesta([alta_06()]), nombre="publico.txt", privado=0)
        with self.assertRaises(frappe.ValidationError) as ctx:
            api.aplicar_respuesta_preregistro(file_url)
        self.assertIn("privado", str(ctx.exception))

    def test_no_se_aplica_un_archivo_privado_de_alguien_mas(self):
        """Además del rol, el permiso de lectura sobre el File: un privado suelto sólo lo lee su
        dueño, así que Tesorería aplica el que ella subió y no el que otro dejó en el sitio."""
        file_url = subir(archivo_respuesta([alta_06()]))     # lo sube Administrator
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.PermissionError):
            api.aplicar_respuesta_preregistro(file_url)

    def test_aplicar_es_de_tesoreria(self):
        file_url = subir(archivo_respuesta([alta_06()]))
        frappe.set_user(REVISOR)
        with self.assertRaises(frappe.PermissionError):
            api.aplicar_respuesta_preregistro(file_url)

    def test_el_permiso_sobre_la_cuenta_tambien_cuenta(self):
        """El rol es global; las User Permissions (por empresa, por proveedor) sólo se aplican
        mirando el documento. Sin esto, quien tuviera el rol de Tesorería podía dar de alta en el
        banco —y con eso habilitar para cobrar— la cuenta de un proveedor que no le toca."""
        self._enviadas(self.cta_06)
        frappe.set_user(TESORERIA)
        file_url = subir(archivo_respuesta([alta_06()]))
        frappe.set_user("Administrator")
        solo_ve(self, "Bank Account", "CUENTA-QUE-NO-ES-ESTA")
        frappe.set_user(TESORERIA)
        for llamada in (lambda: api.descargar_preregistro(),
                        lambda: api.descargar_preregistro(json.dumps([self.cta_12])),
                        lambda: api.aplicar_respuesta_preregistro(file_url),
                        lambda: api.marcar_registrada(json.dumps([self.cta_12]))):
            with self.assertRaises(frappe.PermissionError):
                llamada()
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_06, "estado_preregistro"),
                         "Enviada al banco")
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_12, "estado_preregistro"),
                         "Sin registrar")

    @unittest.skipUnless(os.path.isfile(MUESTRA_REAL), f"no está la muestra real del banco ({MUESTRA_REAL})")
    def test_la_muestra_real_del_banco(self):
        """La única respuesta real que hay: 68 altas de cuentas Banamex de cheques, todas aplicadas.
        Confirma las posiciones del lector sobre datos del banco y no sobre un archivo que arma la
        propia prueba. NO se copia al repo ni se imprime su contenido: son datos de empleados."""
        with open(MUESTRA_REAL, "rb") as f:
            registros = preregistro.leer_respuesta_preregistro(f.read())
        self.assertEqual(len(registros), 68)
        self.assertEqual({r["codigo"] for r in registros}, {"0000"})
        self.assertEqual({r["mensaje"] for r in registros}, {"ALTA APLICADA"})
        self.assertEqual({r["banco"] for r in registros}, {"0002"})
        self.assertEqual({r["tipo_cuenta"] for r in registros}, {"01"})
        # 68 cuentas distintas: si el lector leyera otras posiciones saldría siempre la misma (los
        # doce dígitos que van antes de la cuenta son iguales en los 68 registros).
        self.assertEqual(len({(r["sucursal"], r["cuenta_banamex"]) for r in registros}), 68)
        self.assertTrue(all(re.fullmatch(r"\d{4}", r["sucursal"]) for r in registros))
        self.assertTrue(all(re.fullmatch(r"\d{7}", r["cuenta_banamex"]) for r in registros))
        # Todos los beneficiarios del banco vienen con la estructura NOMBRES,PATERNO/MATERNO.
        self.assertTrue(all(r["beneficiario"].count(",") == 1 and r["beneficiario"].count("/") == 1
                            for r in registros))

    # --- marcar a mano lo que ya estaba dado de alta ----------------------------------------------

    def test_marcar_registrada_es_de_tesoreria(self):
        frappe.set_user(REVISOR)
        with self.assertRaises(frappe.PermissionError):
            api.marcar_registrada(json.dumps([self.cta_12]))
        frappe.set_user(TESORERIA)
        self.assertEqual(api.marcar_registrada(json.dumps([self.cta_12, self.cta_06]))["registradas"], 2)
        for cuenta in (self.cta_12, self.cta_06):
            estado, respuesta = frappe.db.get_value("Bank Account", cuenta,
                                                    ["estado_preregistro", "preregistro_respuesta"])
            self.assertEqual(estado, "Registrada")
            self.assertIn("marcada a mano", respuesta)
            self.assertIn(TESORERIA, respuesta)

    def test_marcar_registrada_exige_cuenta_verificada(self):
        frappe.db.set_value("Bank Account", self.cta_12, "verificada", 0)
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.ValidationError):
            api.marcar_registrada([self.cta_12])
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_12, "estado_preregistro"), "Sin registrar")

    # --- el candado del lote y el borrado del estado ----------------------------------------------

    def test_el_lote_exige_que_la_cuenta_este_pre_registrada(self):
        partidas = [{"factura": self.moral.name, "importe": 100}]
        with self.assertRaises(frappe.ValidationError):
            crear_lotes(pruebas_comun.EMPRESA, FECHA, partidas)
        # Con la cuenta registrada en el banco, el mismo lote sale.
        frappe.db.set_value("Bank Account", self.cta_12, "estado_preregistro", "Registrada")
        (lote,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, partidas)
        self.assertTrue(lote)

    def test_sin_exigir_preregistro_el_lote_pasa(self):
        """El candado se puede apagar en la configuración: mientras Tesorería termine de dar de alta
        a los proveedores que ya existían, los pagos no pueden quedarse parados."""
        frappe.db.set_single_value("Configuracion CxP", "exigir_preregistro", 0)
        self.addCleanup(frappe.db.set_single_value, "Configuracion CxP", "exigir_preregistro", 1)
        (lote,) = crear_lotes(pruebas_comun.EMPRESA, FECHA, [{"factura": self.moral.name, "importe": 100}])
        self.assertEqual(frappe.db.get_value("Bank Account", self.cta_12, "estado_preregistro"), "Sin registrar")
        self.assertTrue(lote)

    def test_cambiar_la_clabe_regresa_la_cuenta_a_sin_registrar(self):
        """Es el mismo punto donde se pierde la verificación de Tesorería: con otra CLABE, el alta
        que autorizó el banco ya no es de esa cuenta."""
        frappe.db.set_value("Bank Account", self.cta_12, {"estado_preregistro": "Registrada",
                                                          "preregistro_respuesta": "0000 ALTA APLICADA"})
        doc = frappe.get_doc("Bank Account", self.cta_12)
        doc.clabe = OTRA_CLABE_12
        doc.save()
        doc.reload()
        self.assertEqual(doc.estado_preregistro, "Sin registrar")
        self.assertFalse(doc.preregistro_respuesta)
        self.assertFalse(doc.preregistro_enviado_el)
        self.assertFalse(doc.verificada)
