"""Lo que la app asegura en cada migración: el grupo de proveedores cuelga de la raíz de verdad."""
import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.setup.instalar import GRUPO_PROVEEDORES, asegurar_grupo_proveedores, raiz_de_grupos_proveedores


class TestInstalar(FrappeTestCase):
    def test_se_encuentra_la_raiz_de_los_grupos_de_proveedores(self):
        """El filtro ['in', ['', None]] se traduce a IN ('', NULL) y en SQL NULL nunca empata dentro
        de un IN: buscando así, la raíz salía vacía y el grupo se habría creado sin padre."""
        raiz = raiz_de_grupos_proveedores()
        self.assertTrue(raiz, "no se encontró la raíz del árbol de Supplier Group")
        self.assertEqual(frappe.db.get_value("Supplier Group", raiz, "is_group"), 1)
        self.assertFalse(frappe.db.get_value("Supplier Group", raiz, "parent_supplier_group"))

    def test_el_grupo_de_cfdi_cuelga_de_la_raiz(self):
        asegurar_grupo_proveedores()   # idempotente: si ya existe, no hace nada
        self.assertEqual(
            frappe.db.get_value("Supplier Group", GRUPO_PROVEEDORES, "parent_supplier_group"),
            raiz_de_grupos_proveedores(),
        )
