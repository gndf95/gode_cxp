"""Flujo de revisión de la factura: transiciones, condiciones y qué puede hacer cada rol."""
import json

import frappe
from frappe.model.workflow import (WorkflowPermissionError, WorkflowTransitionError, apply_workflow,
                                   bulk_workflow_approval)
from frappe.tests.utils import FrappeTestCase

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.pruebas_comun import usuario
from gode_cxp.facturas.crear_factura import crear_factura_desde_cfdi
from gode_cxp.facturas.recepcion import procesar_xml
from gode_cxp.setup import flujo, roles

REVISOR, TESORERIA, CONTA = "prueba.revisor@cxp.local", "prueba.tesoreria@cxp.local", "prueba.conta@cxp.local"
# SYSADMIN: administrador humano que NO es el usuario "Administrator". CONTABLE: alguien de
# contabilidad ajeno a CxP, para comprobar que los Custom Role no le quitan los reportes estándar.
SYSADMIN, CONTABLE = "prueba.sysadmin@cxp.local", "prueba.contable@cxp.local"

# Cuando el rol no tiene la transición, apply_workflow no encuentra la acción y lanza
# WorkflowTransitionError; el guardado posterior lanzaría WorkflowPermissionError. Las dos viven en
# frappe.model.workflow (NO en frappe.exceptions) y las dos heredan de frappe.ValidationError.
SIN_PERMISO = (frappe.PermissionError, WorkflowPermissionError, WorkflowTransitionError)

CAMPOS_QUE_NO_SE_COPIAN = ("cfdi_uuid", "cfdi_recibido", "estado_revision", "recepcion_confirmada",
                           "recepcion_confirmada_por", "recepcion_confirmada_el", "nota_aclaracion")


class TestFlujo(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        pruebas_comun.preparar_sitio_pruebas()

    def setUp(self):
        frappe.set_user("Administrator")
        pruebas_comun.limpiar()
        # limpiar() borra los usuarios de prueba, así que se recrean antes de cada prueba.
        usuario(REVISOR, "CxP Revisor"); usuario(TESORERIA, "CxP Tesoreria"); usuario(CONTA, "CxP Contabilidad")
        usuario(SYSADMIN, "System Manager"); usuario(CONTABLE, "Accounts User")
        self.pi = frappe.get_doc("Purchase Invoice", crear_factura_desde_cfdi(procesar_xml(ejemplos.INGRESO_40, "SAT").name))

    def tearDown(self):
        frappe.set_user("Administrator")

    # ------------------------------------------------------------------ ayudas

    def _como(self, correo, accion):
        frappe.set_user(correo)
        try:
            self.pi.reload()
            apply_workflow(self.pi, accion)
            self.pi.reload()
        finally:
            frappe.set_user("Administrator")

    def _escribir(self, correo, **campos):
        """Guarda campos de la factura COMO el usuario (no con db.set_value): así se comprueba que
        el allow_edit del estado no le impide escribir."""
        frappe.set_user(correo)
        try:
            self.pi.reload()
            for campo, valor in campos.items():
                self.pi.set(campo, valor)
            self.pi.save()
            self.pi.reload()
        finally:
            frappe.set_user("Administrator")

    def _aprobar(self):
        self._como(REVISOR, "Confirmar recepción")
        self._como(TESORERIA, "Aprobar")

    def _otra_factura(self, uuid, folio):
        """Otra factura recibida, para las acciones en bloque (el UUID lleva índice único)."""
        cfdi = procesar_xml(pruebas_comun.xml_con(ejemplos.INGRESO_40, uuid, folio), "SAT")
        return crear_factura_desde_cfdi(cfdi.name)

    def _accion_pendiente(self):
        """Un 'Workflow Action' abierto como los que Frappe crea solo al guardar la factura."""
        accion = frappe.get_doc({"doctype": "Workflow Action", "reference_doctype": "Purchase Invoice",
                                 "reference_name": self.pi.name, "workflow_state": "Recibida",
                                 "status": "Open"})
        accion.append("permitted_roles", {"role": roles.REV})
        return accion.insert(ignore_permissions=True).name

    def _enmendar(self, doc):
        """Imita el botón 'Amend' del escritorio: copia TODO, incluso los campos no_copy
        (frappe/public/js/frappe/model/create_new.js sólo respeta no_copy cuando la copia NO viene
        de un amend), y apunta amended_from a la factura cancelada."""
        copia = frappe.copy_doc(doc)
        copia.amended_from = doc.name
        copia.docstatus = 0
        for hijo in copia.get_all_children():
            hijo.docstatus = 0
        return copia

    # ------------------------------------------------------------------ flujo

    def test_regresar_a_recibida_deshace_la_confirmacion(self):
        """Un bloque mal seleccionado tiene marcha atrás, y quien vuelve a confirmar deja su propio sello."""
        self._como(REVISOR, "Confirmar recepción")
        self._como(REVISOR, "Regresar a recibida")
        self.assertEqual((self.pi.estado_revision, self.pi.recepcion_confirmada, self.pi.recepcion_confirmada_por),
                         ("Recibida", 0, None))
        self._como(TESORERIA, "Confirmar recepción")
        self.assertEqual((self.pi.estado_revision, self.pi.recepcion_confirmada_por), ("Revisada", TESORERIA))

    def test_flujo_completo(self):
        """Dos clics, no cinco: Recibida → 'Confirmar recepción' → Revisada → 'Aprobar'."""
        self.assertEqual(self.pi.estado_revision, "Recibida")
        self._como(REVISOR, "Confirmar recepción")
        self.assertEqual(self.pi.estado_revision, "Revisada")
        # la acción confirma la recepción sola: ya no hay casilla que marcar ni guardado aparte
        self.assertEqual(self.pi.recepcion_confirmada, 1)
        self.assertEqual(self.pi.recepcion_confirmada_por, REVISOR)
        self.assertIsNotNone(self.pi.recepcion_confirmada_el)
        self._como(TESORERIA, "Aprobar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Aprobada", 1))
        self.assertGreater(self.pi.outstanding_amount, 0)

    def test_enviar_a_revision_ya_no_es_un_paso(self):
        """El paso que sobraba se retiró: de 'Recibida' ya no se puede 'Enviar a revisión'."""
        with self.assertRaises(WorkflowTransitionError):
            self._como(REVISOR, "Enviar a revisión")
        self.pi.reload()
        self.assertEqual(self.pi.estado_revision, "Recibida")

    def test_revisor_no_aprueba(self):
        self._como(REVISOR, "Confirmar recepción")
        with self.assertRaises(SIN_PERMISO):
            self._como(REVISOR, "Aprobar")
        self.pi.reload()
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Revisada", 0))

    def test_tesoreria_no_aprueba_lo_que_nadie_ha_recibido(self):
        """Se conserva la separación 'uno confirma la recepción, otro aprueba': una factura Recibida
        no se aprueba de un golpe, aunque quien la mueva tenga los dos roles. Así quedan dos
        registros, el de quién recibió el producto y el de quién autorizó el pago."""
        with self.assertRaises(SIN_PERMISO):
            self._como(TESORERIA, "Aprobar")
        self.pi.reload()
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Recibida", 0))

    def test_confirmar_recepcion_no_pisa_el_sello_que_ya_estaba(self):
        """Si alguien marcó la casilla antes (a mano o desde la ficha), el sello es suyo: la acción
        sólo rellena lo que falta."""
        self._escribir(TESORERIA, recepcion_confirmada=1)
        self.assertEqual(self.pi.recepcion_confirmada_por, TESORERIA)
        self._como(REVISOR, "Confirmar recepción")
        self.assertEqual(self.pi.estado_revision, "Revisada")
        self.assertEqual(self.pi.recepcion_confirmada_por, TESORERIA)

    def test_una_factura_en_revision_sigue_avanzando(self):
        """'En revisión' se conserva en la lista de estados por las facturas que ya estén ahí cuando
        migre producción: desde ahí se confirma la recepción sin casilla, igual que desde Recibida."""
        frappe.db.set_value("Purchase Invoice", self.pi.name, "estado_revision", "En revisión")
        self.pi.reload()
        self._como(REVISOR, "Confirmar recepción")
        self.assertEqual((self.pi.estado_revision, self.pi.recepcion_confirmada), ("Revisada", 1))

    def test_aclaracion_y_rechazo(self):
        with self.assertRaises(frappe.ValidationError):
            self._como(REVISOR, "Pedir aclaración")          # sin nota
        self._escribir(REVISOR, nota_aclaracion="Falta el ticket de recepción")
        self._como(REVISOR, "Pedir aclaración")
        self.assertEqual(self.pi.estado_revision, "En aclaración")
        self._como(REVISOR, "Reanudar")
        # 'Reanudar' devuelve a Recibida: 'En revisión' ya no es un estado al que se llegue
        self.assertEqual(self.pi.estado_revision, "Recibida")
        self._como(TESORERIA, "Rechazar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Rechazada", 0))
        # el UUID sigue bloqueando duplicados
        otra = procesar_xml(ejemplos.INGRESO_40, "SAT")
        self.assertTrue(otra.flags.duplicado)

    def test_tesoreria_escribe_la_nota_y_rechaza_lo_recibido(self):
        """Tesorería debe poder documentar el rechazo sin pasar por el revisor: escribe la nota
        estando en 'Recibida' y rechaza."""
        self._escribir(TESORERIA, nota_aclaracion="El importe no coincide con la orden de compra")
        self._como(TESORERIA, "Rechazar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Rechazada", 0))
        self.assertEqual(self.pi.nota_aclaracion, "El importe no coincide con la orden de compra")

    def test_tesoreria_rechaza_lo_ya_revisado(self):
        """Rechazar sigue disponible después de que alguien confirmó la recepción."""
        self._como(REVISOR, "Confirmar recepción")
        self._escribir(TESORERIA, nota_aclaracion="Factura duplicada")
        self._como(TESORERIA, "Rechazar")
        self.assertEqual(self.pi.estado_revision, "Rechazada")

    def test_tesoreria_tambien_confirma_la_recepcion(self):
        """'Confirmar recepción' es de los dos roles: Tesorería no depende del revisor."""
        self._como(TESORERIA, "Confirmar recepción")
        self.assertEqual((self.pi.estado_revision, self.pi.recepcion_confirmada_por), ("Revisada", TESORERIA))

    def test_acciones_en_bloque_desde_la_lista(self):
        """Lo que de verdad ahorra clics con ~1000 facturas al mes: marcar varias en la lista y
        aplicarles la acción de golpe. Lo mueve la función nativa de Frappe, así que el flujo nuevo
        tiene que funcionar tal cual por ahí."""
        otra = self._otra_factura("11111111-2222-3333-4444-555555555555", "77")
        nombres = json.dumps([self.pi.name, otra])
        frappe.set_user(REVISOR)
        try:
            bulk_workflow_approval(nombres, "Purchase Invoice", "Confirmar recepción")
        finally:
            frappe.set_user("Administrator")
        for name in (self.pi.name, otra):
            self.assertEqual(frappe.db.get_value("Purchase Invoice", name,
                                                 ["estado_revision", "recepcion_confirmada"]),
                             ("Revisada", 1), name)
        frappe.set_user(TESORERIA)
        try:
            bulk_workflow_approval(nombres, "Purchase Invoice", "Aprobar")
        finally:
            frappe.set_user("Administrator")
        for name in (self.pi.name, otra):
            self.assertEqual(frappe.db.get_value("Purchase Invoice", name, ["estado_revision", "docstatus"]),
                             ("Aprobada", 1), name)

    def test_al_cambiar_el_flujo_se_cierran_las_acciones_pendientes(self):
        """Frappe crea un 'Workflow Action' abierto por factura con los roles que en ese momento
        podían moverla (create_workflow_actions_for_roles); el registro guarda el ESTADO, no la
        acción. Al retirar una transición, los que quedan abiertos prometen un permiso que ya no
        existe: asegurar_flujo los cierra y el siguiente guardado crea el que toque."""
        self.addCleanup(flujo.asegurar_flujo)
        if not frappe.db.exists("Workflow Action Master", "Enviar a revisión"):
            frappe.get_doc({"doctype": "Workflow Action Master",
                            "workflow_action_name": "Enviar a revisión"}).insert(ignore_permissions=True)
        wf = frappe.get_doc("Workflow", flujo.NOMBRE)
        wf.append("transitions", {"state": "Recibida", "action": "Enviar a revisión",
                                  "next_state": "En revisión", "allowed": roles.REV,
                                  "allow_self_approval": 1, "condition": ""})
        wf.flags.ignore_permissions = True
        wf.save()
        vieja = self._accion_pendiente()
        flujo.asegurar_flujo()
        self.assertEqual(frappe.db.get_value("Workflow Action", vieja, "status"), "Completed")
        self.assertNotIn("Enviar a revisión",
                         {t.action for t in frappe.get_doc("Workflow", flujo.NOMBRE).transitions})
        # y es idempotente: con el flujo ya en su sitio, lo que esté abierto se queda abierto
        nueva = self._accion_pendiente()
        flujo.asegurar_flujo()
        self.assertEqual(frappe.db.get_value("Workflow Action", nueva, "status"), "Open")

    def test_error_de_lectura_no_se_aprueba(self):
        frappe.db.set_value("Purchase Invoice", self.pi.name, "estado_revision", "Error de lectura")
        self.pi.reload()
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.ValidationError):
            self.pi.submit()

    # ------------------------------------------------------- estados y edición

    def test_todos_los_estados_los_editan_revisor_y_tesoreria(self):
        """allow_edit es obligatorio y admite un solo rol por fila. El navegador deja el formulario
        en solo lectura cuando el usuario no tiene ninguno de los roles allow_edit del estado
        (frappe/public/js/frappe/model/workflow.js::is_read_only), así que un rol distinto por
        estado dejaría a Tesorería sin poder escribir en 'En revisión'."""
        wf = frappe.get_doc("Workflow", flujo.NOMBRE)
        por_estado = {}
        for fila in wf.states:
            por_estado.setdefault(fila.state, set()).add(fila.allow_edit)
        for correo in (REVISOR, TESORERIA, SYSADMIN):
            frappe.set_user(correo)
            try:
                mis_roles = set(frappe.get_roles())
                for estado, permitidos in por_estado.items():
                    self.assertTrue(permitidos & mis_roles, f"{correo} no puede editar en '{estado}'")
            finally:
                frappe.set_user("Administrator")

    def test_los_roles_que_escriben_arrastran_el_rol_editor(self):
        """El rol CxP Editor no se asigna a mano: lo pone la app a quien pueda escribir facturas."""
        for correo in (REVISOR, TESORERIA):
            self.assertIn(roles.EDITOR, {r.role for r in frappe.get_doc("User", correo).roles}, correo)
        self.assertNotIn(roles.EDITOR, {r.role for r in frappe.get_doc("User", CONTA).roles})

    def test_los_system_manager_tambien_llevan_el_rol_editor(self):
        """Un administrador humano (System Manager que no es 'Administrator') no hereda los roles
        CxP: sin CxP Editor abriría la factura en solo lectura y no podría corregirla a mano."""
        self.assertIn("System Manager", roles.ROLES_QUE_ESCRIBEN)
        self.assertIn(roles.EDITOR, {r.role for r in frappe.get_doc("User", SYSADMIN).roles}, SYSADMIN)
        # y el camino de la migración, para los administradores que ya existían
        filtro = {"parenttype": "User", "parent": SYSADMIN, "role": roles.EDITOR}
        frappe.db.delete("Has Role", filtro)
        frappe.clear_cache(user=SYSADMIN)
        roles.asegurar_rol_editor()
        self.assertTrue(frappe.db.exists("Has Role", filtro))

    def test_asegurar_rol_editor_repara_a_los_usuarios_viejos(self):
        """Camino de la migración: los usuarios que ya existían antes de esta versión no pasaron por
        el hook de User, así que asegurar_rol_editor tiene que encontrarlos y ponerles el rol."""
        filtro = {"parenttype": "User", "parent": REVISOR, "role": roles.EDITOR}
        frappe.db.delete("Has Role", filtro)          # se salta el hook, como un usuario de antes
        frappe.clear_cache(user=REVISOR)
        self.assertFalse(frappe.db.exists("Has Role", filtro))
        roles.asegurar_rol_editor()
        self.assertTrue(frappe.db.exists("Has Role", filtro))

    # ------------------------------------------------------------------ amend

    def test_campos_de_revision_no_se_copian(self):
        meta = frappe.get_meta("Purchase Invoice")
        for campo in CAMPOS_QUE_NO_SE_COPIAN:
            self.assertEqual(meta.get_field(campo).no_copy, 1, campo)

    def test_amend_no_arrastra_cfdi(self):
        """Enmendar una factura cancelada no puede arrastrar el UUID (índice único → duplicado) ni
        el estado 'Aprobada' (sin transición que lo justifique): la enmienda se revisa de nuevo."""
        self._aprobar()
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Aprobada", 1))
        frappe.set_user(TESORERIA)
        try:
            self.pi.reload()
            self.pi.cancel()
            enmendada = self._enmendar(self.pi)
            enmendada.save()
        finally:
            frappe.set_user("Administrator")
        self.assertEqual(enmendada.amended_from, self.pi.name)
        self.assertEqual(enmendada.docstatus, 0)
        self.assertFalse(enmendada.cfdi_uuid)
        self.assertEqual(enmendada.estado_revision, "Recibida")
        self.assertFalse(enmendada.recepcion_confirmada)
        self.assertFalse(enmendada.nota_aclaracion)
        # releída de la base, no el objeto en memoria: el sello de la recepción también se borra
        guardada = frappe.get_doc("Purchase Invoice", enmendada.name)
        self.assertFalse(guardada.recepcion_confirmada_por)
        self.assertFalse(guardada.recepcion_confirmada_el)
        # y el CFDI apunta a la enmienda, no a la factura cancelada
        self.assertEqual(guardada.cfdi_recibido, self.pi.cfdi_recibido)
        self.assertEqual(frappe.db.get_value("CFDI Recibido", guardada.cfdi_recibido, "factura"), guardada.name)

    # ---------------------------------------------------- permisos del espacio

    def test_los_roles_cxp_abren_los_reportes_del_workspace(self):
        """Los reportes estándar traen sus propios roles y Report.is_permitted() los respeta."""
        for reporte in roles.REPORTES:
            self.assertTrue(frappe.db.exists("Report", reporte), reporte)
            for correo in (REVISOR, TESORERIA, CONTA):
                frappe.set_user(correo)
                try:
                    self.assertTrue(frappe.get_doc("Report", reporte).is_permitted(), f"{correo} / {reporte}")
                finally:
                    frappe.set_user("Administrator")

    def test_el_custom_role_no_le_quita_los_reportes_a_accounts_user(self):
        """Report.is_permitted() SUSTITUYE los roles del reporte por los del Custom Role, así que
        asegurar_reportes guarda la unión: quien ya abría 'Accounts Payable' no lo puede perder."""
        for reporte in roles.REPORTES:
            frappe.set_user(CONTABLE)
            try:
                self.assertTrue(frappe.get_doc("Report", reporte).is_permitted(), f"{CONTABLE} / {reporte}")
            finally:
                frappe.set_user("Administrator")

    def test_tesoreria_lee_la_configuracion_cxp(self):
        frappe.set_user(TESORERIA)
        try:
            self.assertTrue(frappe.has_permission("Configuracion CxP", "read"))
            self.assertFalse(frappe.has_permission("Configuracion CxP", "write"))
        finally:
            frappe.set_user("Administrator")

    def test_contabilidad_solo_lee(self):
        frappe.set_user(CONTA)
        self.assertTrue(frappe.has_permission("Purchase Invoice", "read", self.pi.name))
        self.assertFalse(frappe.has_permission("Purchase Invoice", "write", self.pi.name))
        self.assertFalse(frappe.has_permission("CFDI Recibido", "create"))
        frappe.set_user(REVISOR)
        self.assertTrue(frappe.has_permission("CFDI Recibido", "create"))
        self.assertFalse(frappe.has_permission("Purchase Invoice", "submit", self.pi.name))

    def test_workspace_y_perfil(self):
        self.assertTrue(frappe.db.exists("Workspace", "Cuentas por Pagar"))
        self.assertTrue(frappe.db.exists("Module Profile", "Cuentas por Pagar"))
        bloqueados = frappe.get_all("Block Module", filters={"parent": "Cuentas por Pagar", "parenttype": "Module Profile"}, pluck="module")
        self.assertIn("HR", bloqueados)
        self.assertNotIn("Accounts", bloqueados)
        self.assertNotIn("Cuentas por Pagar", bloqueados)
