// Botones del lote de pago: lo que Tesorería puede hacer en cada estado.
// El estado manda, no el docstatus: un lote enviado recorre Autorizado → Exportado → Transmitido →
// (Aplicado | Parcial | Rechazado) y en cada paso hay un botón distinto.
frappe.ui.form.on("Lote de Pago", {
	refresh(frm) {
		const tesoreria = frappe.user_roles.includes("CxP Tesoreria") || frappe.user_roles.includes("System Manager");
		// Enviar un lote ES autorizarlo: la etiqueta del botón estándar lo dice con esas palabras.
		// Sólo cuando no hay cambios sin guardar, para no taparle el "Guardar" a un borrador sucio.
		if (frm.doc.docstatus === 0 && tesoreria && !frm.is_new() && !frm.is_dirty()) {
			frm.page.set_primary_action(__("Autorizar lote"), () => frm.savesubmit());
		}
		if (frm.doc.archivo_tef) {
			frm.add_custom_button(__("Descargar archivo"), () => window.open(frm.doc.archivo_tef));
		}
		if (!tesoreria || frm.doc.docstatus !== 1) return;

		const llamar = (method, args, despues) => frappe.call({
			method: method, args: args, freeze: true,
			callback: (r) => { frm.reload_doc(); if (despues) despues(r); },
		});

		if (["Autorizado", "Exportado"].includes(frm.doc.estado_lote)) {
			frm.add_custom_button(__("Generar archivo TEF"), () => {
				llamar("gode_cxp.pagos.api.generar_archivo", { lote: frm.doc.name },
					(r) => { if (r.message) window.open(r.message.file_url); });
			}, __("Banco"));
		}
		if (frm.doc.estado_lote === "Exportado") {
			frm.add_custom_button(__("Marcar transmitido"), () => {
				frappe.prompt({ fieldname: "autorizacion", fieldtype: "Data", label: __("Autorización de BancaNet"), reqd: 1 },
					(v) => llamar("gode_cxp.pagos.api.marcar_transmitido", { lote: frm.doc.name, autorizacion: v.autorizacion }),
					__("Lote transmitido"));
			}, __("Banco"));
		}
		// El Resultado Bancario lo trae la Task 6: mientras el DocType no exista, can_create() es
		// false y el botón no se dibuja (así el formulario no truena en un sitio a medio migrar).
		if (["Transmitido", "Aplicado", "Parcial", "Rechazado"].includes(frm.doc.estado_lote)
			&& frappe.model.can_create("Resultado Bancario")) {
			frm.add_custom_button(__("Capturar resultado del banco"),
				() => frappe.new_doc("Resultado Bancario", { lote: frm.doc.name }), __("Banco"));
		}
		if (["Parcial", "Rechazado"].includes(frm.doc.estado_lote)) {
			frm.add_custom_button(__("Nuevo lote con los pendientes"), () => {
				llamar("gode_cxp.pagos.api.nuevo_lote_pendientes", { lote: frm.doc.name },
					(r) => r.message
						? frappe.set_route("Form", "Lote de Pago", r.message)
						: frappe.msgprint(__("No queda nada pendiente.")));
			}, __("Banco"));
		}
	},
});
