// Botones del resultado del banco: el alta desde el lote para capturar a mano, la carga del archivo
// que devuelve BancaNet y el visto bueno cuando el archivo no cuadra con el lote.
// El estado manda: un resultado ya Aplicado no se vuelve a tocar.
frappe.ui.form.on("Resultado Bancario", {
	refresh(frm) {
		const tesoreria = frappe.user_roles.includes("CxP Tesoreria") || frappe.user_roles.includes("System Manager");
		if (!tesoreria) return;

		// Se llega aquí desde "Capturar resultado del banco" del lote: el servidor arma el resultado
		// con un movimiento por transferencia y el formulario se abre sobre el documento ya guardado.
		// La bandera evita pedirlo dos veces si el formulario se refresca antes de cambiar de ruta.
		if (frm.is_new() && frm.doc.lote && !(frm.doc.movimientos || []).length && !frm.__cxp_captura_pedida) {
			frm.__cxp_captura_pedida = true;
			frappe.call({
				method: "gode_cxp.banamex.api.crear_captura_manual",
				args: { lote: frm.doc.lote },
				freeze: true,
				callback: (r) => {
					if (r.message) frappe.set_route("Form", "Resultado Bancario", r.message);
				},
				error: () => { frm.__cxp_captura_pedida = false; },
			});
			return;
		}
		if (frm.is_new()) return;

		if (frm.doc.estado !== "Aplicado") {
			frm.add_custom_button(__("Cargar archivo del portal"), () => {
				// El archivo trae las cuentas de los proveedores: se sube como adjunto PRIVADO del
				// resultado (make_attachments_public: false) y el servidor lo exige así para leerlo.
				new frappe.ui.FileUploader({
					doctype: frm.doc.doctype,
					docname: frm.doc.name,
					frm: frm,
					allow_multiple: false,
					make_attachments_public: false,
					disable_file_browser: true,
					restrictions: { allowed_file_types: [".csv", ".txt"] },
					on_success: (archivo) => frappe.call({
						method: "gode_cxp.banamex.api.importar_respuesta",
						args: { resultado: frm.doc.name, file_url: archivo.file_url },
						freeze: true,
						callback: (r) => {
							frm.reload_doc();
							if (!r.message || !r.message.resumen) return;
							frappe.msgprint({
								title: __("Respuesta del banco"),
								// El resumen sólo lleva un segundo renglón cuando hay diferencias (el
								// estado del formulario todavía no se recargó, así que no sirve mirarlo).
								indicator: r.message.resumen.includes("\n") ? "orange" : "blue",
								message: frappe.utils.escape_html(r.message.resumen).replace(/\n/g, "<br>"),
							});
						},
					}),
				});
			}, __("Banco"));
			// "Aplicar pagos" (gode_cxp.banamex.api.aplicar) NO está aquí: ese método llega con la
			// creación de los Payment Entry y un botón que llama a un método inexistente sólo sirve
			// para sacarle un error a Tesorería. Se agrega en la misma tarea que lo escriba.
		}

		if (frm.doc.estado === "Con diferencias") {
			frm.add_custom_button(__("Marcar revisado"), () => frappe.confirm(
				__("Se dará por revisada la diferencia entre lo que contestó el banco y el lote. ¿Continuar?"),
				() => frappe.call({
					method: "gode_cxp.banamex.api.marcar_revisado",
					args: { resultado: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				})));
		}
	},
});
