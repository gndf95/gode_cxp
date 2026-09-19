// Reporte "Pagos por lote COI": filtros de fecha de pago (obligatorios), lote opcional y
// "solo aplicados" (por omisión sólo lo que el banco ya pagó).
frappe.query_reports["Pagos por lote COI"] = {
	filters: [
		{
			fieldname: "desde",
			label: __("Desde"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "hasta",
			label: __("Hasta"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "lote",
			label: __("Lote"),
			fieldtype: "Link",
			options: "Lote de Pago",
		},
		{
			fieldname: "solo_aplicados",
			label: __("Solo aplicados"),
			fieldtype: "Check",
			default: 1,
		},
	],
};
