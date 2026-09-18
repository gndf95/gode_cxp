"""Flujo de revisión de la factura: transiciones, condiciones y qué puede hacer cada rol."""
import frappe
from frappe.model.workflow import WorkflowPermissionError, WorkflowTransitionError, apply_workflow
from frappe.tests.utils import FrappeTestCase

from gode_cxp.cfdi import ejemplos
from gode_cxp.facturas import pruebas_comun
from gode_cxp.facturas.crear_factura import crear_factura_desde_cfdi
from gode_cxp.facturas.recepcion import procesar_xml

REVISOR, TESORERIA, CONTA = "prueba.revisor@cxp.local", "prueba.tesoreria@cxp.local", "prueba.conta@cxp.local"

# Cuando el rol no tiene la transición, apply_workflow no encuentra la acción y lanza
# WorkflowTransitionError; el guardado posterior lanzaría WorkflowPermissionError. Las dos viven en
# frappe.model.workflow (NO en frappe.exceptions) y las dos heredan de frappe.ValidationError.
SIN_PERMISO = (frappe.PermissionError, WorkflowPermissionError, WorkflowTransitionError)


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

    def _como(self, correo, accion):
        frappe.set_user(correo)
        try:
            self.pi.reload()
            apply_workflow(self.pi, accion)
            self.pi.reload()
        finally:
            frappe.set_user("Administrator")

    def test_flujo_completo(self):
        self.assertEqual(self.pi.estado_revision, "Recibida")
        self._como(REVISOR, "Enviar a revisión")
        self.assertEqual(self.pi.estado_revision, "En revisión")
        frappe.set_user(REVISOR)
        self.pi.reload(); self.pi.recepcion_confirmada = 1; self.pi.save()
        self.pi.reload()
        self.assertEqual(self.pi.recepcion_confirmada_por, REVISOR)
        self.assertIsNotNone(self.pi.recepcion_confirmada_el)
        frappe.set_user("Administrator")
        self._como(REVISOR, "Confirmar recepción")
        self.assertEqual(self.pi.estado_revision, "Revisada")
        self._como(TESORERIA, "Aprobar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Aprobada", 1))
        self.assertGreater(self.pi.outstanding_amount, 0)

    def test_revisor_no_aprueba(self):
        self._como(REVISOR, "Enviar a revisión")
        frappe.set_user(REVISOR); self.pi.reload(); self.pi.recepcion_confirmada = 1; self.pi.save(); frappe.set_user("Administrator")
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
        frappe.db.set_value("Purchase Invoice", self.pi.name, "nota_aclaracion", "Falta el ticket de recepción")
        self._como(REVISOR, "Pedir aclaración")
        self.assertEqual(self.pi.estado_revision, "En aclaración")
        self._como(REVISOR, "Reanudar")
        self._como(TESORERIA, "Rechazar")
        self.assertEqual((self.pi.estado_revision, self.pi.docstatus), ("Rechazada", 0))
        # el UUID sigue bloqueando duplicados
        otra = procesar_xml(ejemplos.INGRESO_40, "SAT")
        self.assertTrue(otra.flags.duplicado)

    def test_error_de_lectura_no_se_aprueba(self):
        frappe.db.set_value("Purchase Invoice", self.pi.name, "estado_revision", "Error de lectura")
        self.pi.reload()
        frappe.set_user(TESORERIA)
        with self.assertRaises(frappe.ValidationError):
            self.pi.submit()

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
