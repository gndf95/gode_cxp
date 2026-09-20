"""Lo que la app asegura en cada migración: el grupo de proveedores cuelga de la raíz de verdad."""
import json

import frappe
from frappe.tests.utils import FrappeTestCase

from gode_cxp.setup.instalar import (COLUMNAS_CUENTAS_BANCARIAS, FUERA_DE_LA_LISTA_CUENTAS,
                                     GRUPO_PROVEEDORES, asegurar_grupo_proveedores,
                                     asegurar_lista_cuentas_bancarias, raiz_de_grupos_proveedores)


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


class TestListaCuentasBancarias(FrappeTestCase):
    """La lista de Bank Account venía con Compañía y 'Cuenta de la compañía', que en una cuenta de
    proveedor van siempre vacías. Lo que Tesorería necesita ver es a quién se le paga, por dónde y
    si la cuenta ya se puede usar."""

    def test_las_columnas_utiles_estan_en_el_meta(self):
        asegurar_lista_cuentas_bancarias()      # idempotente: se llama en cada migración
        meta = frappe.get_meta("Bank Account")
        for columna in COLUMNAS_CUENTAS_BANCARIAS:
            if columna == "status_field":       # el indicador, que no es un campo
                continue
            self.assertEqual(meta.get_field(columna).in_list_view, 1, columna)
        for campo in FUERA_DE_LA_LISTA_CUENTAS:
            self.assertFalse(meta.get_field(campo).in_list_view, campo)

    def test_la_lista_se_configura_por_codigo(self):
        """`in_list_view` no alcanza: el escritorio recorta a 4 o 6 columnas según el ancho de la
        pantalla (frappe/public/js/frappe/list/list_view.js::setup_columns), y el orden lo fija
        `List View Settings.fields` —que sólo reordena lo que ya viene marcado en el meta."""
        asegurar_lista_cuentas_bancarias()
        ajustes = frappe.get_doc("List View Settings", "Bank Account")
        self.assertEqual([f["fieldname"] for f in json.loads(ajustes.fields)], COLUMNAS_CUENTAS_BANCARIAS)
        self.assertGreaterEqual(int(ajustes.total_fields), len(COLUMNAS_CUENTAS_BANCARIAS) + 2)
