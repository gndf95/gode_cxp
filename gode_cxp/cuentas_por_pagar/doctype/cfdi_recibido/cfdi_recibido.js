// CFDI Recibido: crear la factura de compra a mano (o reintentarla) y saltar a la que ya existe.
frappe.ui.form.on("CFDI Recibido", {
	refresh(frm) {
		// Si ya hay factura no se ofrece crearla otra vez: para eso está "Ver factura".
		if (
			!frm.is_new() &&
			!frm.doc.factura &&
			["Nuevo", "Error"].includes(frm.doc.estado) &&
			["I", "E"].includes(frm.doc.tipo_comprobante)
		) {
			frm.add_custom_button(__("Crear factura de compra"), () => {
				frappe.call({
					method: "gode_cxp.facturas.api.crear_factura",
					args: { cfdi: frm.doc.name },
					freeze: true,
					freeze_message: __("Creando la factura…"),
					callback: () => {
						// Recargar en vez de saltar a la factura: el usuario ve el CFDI ya en "Con
						// factura" y decide si abrirla con "Ver factura".
						frm.reload_doc();
					},
				});
			}).addClass("btn-primary");
		}
		if (frm.doc.factura) {
			frm.add_custom_button(__("Ver factura"), () =>
				frappe.set_route("Form", "Purchase Invoice", frm.doc.factura)
			);
		}
	},
});
