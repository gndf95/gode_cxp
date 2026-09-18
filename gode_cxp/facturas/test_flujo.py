"""Flujo de revisión de la factura: transiciones, condiciones y qué puede hacer cada rol."""
import frappe
from frappe.model.workflow import WorkflowPermissionError, WorkflowTransitionError, apply_workflow
from frappe.tests.utils import FrappeTestCase

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.crear_factura import crear_factura_desde_cfdi
from gode_cxp.facturas.recepcion import procesar_xml
from gode_cxp.setup import flujo, roles

REVISOR, TESORERIA, CONTA = "prueba.revisor@cxp.local", "prueba.tesoreria@cxp.local", "prueba.conta@cxp.local"

# Cuando el rol no tiene la transición, apply_workflow no encuentra la acción y lanza
# WorkflowTransitionError; el guardado posterior lanzaría WorkflowPermissionError. Las dos viven en
# frappe.model.workflow (NO en frappe.exceptions) y las dos heredan de frappe.ValidationError.
SIN_PERMISO = (frappe.PermissionError, WorkflowPermissionError, WorkflowTransitionError)

CAMPOS_QUE_NO_SE_COPIAN = ("cfdi_uuid", "cfdi_recibido", "estado_revision", "recepcion_confirmada",
                           "recepcion_confirmada_por", "recepcion_confirmada_el", "nota_aclaracion")


def usuario(correo, rol):
    if not frappe.db.exists("User", correo):
        u = frappe.get_doc({"doctype": "User", "email": correo, "first_name": correo.split("@")[0], "send_welcome_email": 0})
        u.append("roles", {"role": rol})
        u.insert(ignore_permissions=True)
    return correo


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
        self._como(REVISOR, "Enviar a revisión")
        self._escribir(REVISOR, recepcion_confirmada=1)
        self._como(REVISOR, "Confirmar recepción")
        self._como(TESORERIA, "Aprobar")

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

    def test_flujo_completo(self):
        self.assertEqual(self.pi.estado_revision, "Recibida")
        self._como(REVISOR, "Enviar a revisión")
        self.assertEqual(self.pi.estado_revision, "En revisión")
        self._escribir(REVISOR, recepcion_confirmada=1)
        self.assertEqual(self.pi.recepcion_confirmada_por, REVISOR)
        self.assertIsNotNone(self.pi.recepcion_confirmada_el)
        self._como(REVISOR, "Confirmar recepción")
        self.assertEqual(self.pi.estado_revision, "Revisada")
        self._como(TESORERIA, "Aprobar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Aprobada", 1))
        self.assertGreater(self.pi.outstanding_amount, 0)

    def test_revisor_no_aprueba(self):
        self._como(REVISOR, "Enviar a revisión")
        self._escribir(REVISOR, recepcion_confirmada=1)
        self._como(REVISOR, "Confirmar recepción")
        with self.assertRaises(SIN_PERMISO):
            self._como(REVISOR, "Aprobar")
        self.pi.reload()
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Revisada", 0))

    def test_confirmar_recepcion_exige_la_marca(self):
        self._como(REVISOR, "Enviar a revisión")
        with self.assertRaises(frappe.ValidationError):
            self._como(REVISOR, "Confirmar recepción")

    def test_aclaracion_y_rechazo(self):
        self._como(REVISOR, "Enviar a revisión")
        with self.assertRaises(frappe.ValidationError):
            self._como(REVISOR, "Pedir aclaración")          # sin nota
        self._escribir(REVISOR, nota_aclaracion="Falta el ticket de recepción")
        self._como(REVISOR, "Pedir aclaración")
        self.assertEqual(self.pi.estado_revision, "En aclaración")
        self._como(REVISOR, "Reanudar")
        self._como(TESORERIA, "Rechazar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Rechazada", 0))
        # el UUID sigue bloqueando duplicados
        otra = procesar_xml(ejemplos.INGRESO_40, "SAT")
        self.assertTrue(otra.flags.duplicado)

    def test_tesoreria_escribe_la_nota_en_revision_y_rechaza(self):
        """Tesorería debe poder documentar el rechazo sin pasar por el revisor: escribe la nota
        estando en 'En revisión' (estado cuyo allow_edit era sólo del revisor) y rechaza."""
        self._como(REVISOR, "Enviar a revisión")
        self._escribir(TESORERIA, nota_aclaracion="El importe no coincide con la orden de compra")
        self._como(TESORERIA, "Rechazar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Rechazada", 0))
        self.assertEqual(self.pi.nota_aclaracion, "El importe no coincide con la orden de compra")

    def test_tesoreria_confirma_la_recepcion_en_revision(self):
        """La transición 'Confirmar recepción' también es de Tesorería: tiene que poder marcar la
        casilla en 'En revisión', no sólo aplicar la acción."""
        self._como(REVISOR, "Enviar a revisión")
        self._escribir(TESORERIA, recepcion_confirmada=1)
        self.assertEqual(self.pi.recepcion_confirmada_por, TESORERIA)
        self._como(TESORERIA, "Confirmar recepción")
        self.assertEqual(self.pi.estado_revision, "Revisada")

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
        for correo in (REVISOR, TESORERIA):
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
