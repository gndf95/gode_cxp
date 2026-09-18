# gode_cxp

App de Frappe/ERPNext para las cuentas por pagar de GODE (Grupo Garena). Cubre la
recepción de facturas de proveedores con su CFDI, el flujo de autorización, los lotes
de pago para Banamex y la conciliación posterior. Se instala sobre un sitio de ERPNext
15 con HRMS y no reemplaza a los DocTypes estándar: los extiende con campos, roles,
flujo de trabajo y un workspace propios, que la app vuelve a dejar en su lugar en cada
migración (`after_migrate`).

La documentación de diseño —alcance, decisiones y el detalle de cada pieza— vive en el
repositorio de operaciones, en
`frappe-hr-ops/docs/superpowers/specs/2026-09-17-cuentas-por-pagar-design.md`. Ese
documento es la referencia para entender por qué la app está hecha así; este repositorio
solo contiene el código.
