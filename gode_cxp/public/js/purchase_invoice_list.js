// Acción "Crear lote de pago" sobre las facturas seleccionadas en la lista de facturas de compra.
//
// ERPNext ya define frappe.listview_settings["Purchase Invoice"] (add_fields, get_indicator y un
// onload con las acciones masivas de Purchase Receipt y Payment Entry), y los dos archivos llegan al
// navegador pegados en el mismo __list_js, el de ERPNext primero. Así que aquí se EXTIENDE ese
// objeto —se encadena su onload— en vez de reasignarlo, o se perderían los indicadores de estado.
frappe.listview_settings["Purchase Invoice"] = frappe.listview_settings["Purchase Invoice"] || {};
const cxp_ajustes_pi = frappe.listview_settings["Purchase Invoice"];
const cxp_onload_previo = cxp_ajustes_pi.onload;

cxp_ajustes_pi.onload = function (listview) {
	if (cxp_onload_previo) cxp_onload_previo(listview);
	if (!(frappe.user_roles.includes("CxP Tesoreria") || frappe.user_roles.includes("System Manager"))) return;
	listview.page.add_action_item(__("Crear lote de pago"), () => {
		const filas = listview.get_checked_items();
		if (!filas.length) {
			frappe.msgprint(__("Selecciona al menos una factura aprobada."));
			return;
		}
		// La empresa sale de las facturas elegidas, no del default del usuario: si no coinciden, el
		// lote se armaría con la empresa equivocada y la validación lo rechazaría sin explicar por qué.
		const empresas = Array.from(new Set(filas.map((d) => d.company).filter(Boolean)));
		if (empresas.length > 1) {
			frappe.msgprint(__("Las facturas seleccionadas son de varias empresas: haz un lote por empresa."));
			return;
		}
		const company = empresas[0] || frappe.defaults.get_user_default("Company");
		if (!company) {
			frappe.msgprint(__("No se pudo determinar la empresa de las facturas seleccionadas."));
			return;
		}
		const seleccion = filas.map((d) => d.name);
		frappe.call({
			method: "gode_cxp.pagos.api.facturas_pagables",
			args: { company: company },
			freeze: true,
			callback: (r) => {
				const pagables = (r.message || []).filter((f) => seleccion.includes(f.name));
				const fuera = seleccion.filter((n) => !pagables.some((f) => f.name === n));
				if (!pagables.length) {
					frappe.msgprint(__("Ninguna de las seleccionadas se puede pagar (deben estar Aprobadas, con saldo, en MXN y fuera de otro lote)."));
					return;
				}
				cxp_dialogo_lote(company, pagables, fuera, listview);
			},
		});
	});
};

function cxp_dialogo_lote(company, pagables, fuera, listview) {
	const d = new frappe.ui.Dialog({
		title: __("Nuevo lote de pago"),
		size: "large",
		fields: [
			{ fieldname: "fecha_pago", fieldtype: "Date", label: __("Fecha de pago"), reqd: 1, default: frappe.datetime.get_today() },
			{ fieldname: "aviso", fieldtype: "HTML", options: fuera.length ? `<p class="text-muted">${__("Se omiten (no pagables): {0}", [frappe.utils.escape_html(fuera.join(", "))])}</p>` : "" },
			{
				fieldname: "partidas", fieldtype: "Table", label: __("Facturas"), cannot_add_rows: true, in_place_edit: true,
				data: pagables.map((f) => ({
					factura: f.name, proveedor: f.supplier_name || f.supplier, folio: f.bill_no,
					saldo: f.outstanding_amount, importe: f.outstanding_amount,
				})),
				fields: [
					{ fieldname: "factura", fieldtype: "Data", label: __("Factura"), read_only: 1, in_list_view: 1, columns: 3 },
					{ fieldname: "proveedor", fieldtype: "Data", label: __("Proveedor"), read_only: 1, in_list_view: 1, columns: 3 },
					{ fieldname: "folio", fieldtype: "Data", label: __("Folio"), read_only: 1, in_list_view: 1, columns: 2 },
					{ fieldname: "saldo", fieldtype: "Currency", label: __("Saldo"), read_only: 1, in_list_view: 1, columns: 2 },
					{ fieldname: "importe", fieldtype: "Currency", label: __("A pagar"), in_list_view: 1, columns: 2 },
				],
			},
		],
		primary_action_label: __("Crear lote(s)"),
		primary_action(valores) {
			// Importe 0 = "esta vez no": la fila se queda fuera del lote en lugar de reventar la validación.
			const partidas = (valores.partidas || []).filter((p) => p.importe > 0)
				.map((p) => ({ factura: p.factura, importe: p.importe }));
			if (!partidas.length) {
				frappe.msgprint(__("Captura un importe mayor que cero en al menos una factura."));
				return;
			}
			frappe.call({
				method: "gode_cxp.pagos.api.crear_lotes",
				args: { company: company, fecha_pago: valores.fecha_pago, partidas: JSON.stringify(partidas) },
				freeze: true,
				callback: (r) => {
					d.hide();
					const enlaces = (r.message || [])
						.map((n) => `<a href="/app/lote-de-pago/${encodeURIComponent(n)}">${frappe.utils.escape_html(n)}</a>`)
						.join(", ");
					// Un lote por naturaleza: un archivo del banco no mezcla 06 con 12, así que una
					// selección de facturas puede acabar en dos lotes y hay que enseñar los dos.
					frappe.msgprint({ title: __("Lotes creados"), message: enlaces, indicator: "green" });
					listview.refresh();
				},
			});
		},
	});
	d.show();
}
