// Reporte "Facturas en lotes": facturas que un lote sigue apartando y en qué estado están.
frappe.query_reports["Facturas en lotes"] = {
	filters: [
		{
			fieldname: "proveedor",
			label: __("Proveedor"),
			fieldtype: "Link",
			options: "Supplier",
		},
		{
			fieldname: "estado_lote",
			label: __("Estado del lote"),
			fieldtype: "Select",
			options: "\nPreparado\nAutorizado\nExportado\nTransmitido\nAplicado\nParcial\nRechazado\nCancelado",
		},
	],
};
