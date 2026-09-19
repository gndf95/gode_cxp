// Botones del resultado del banco: el alta desde el lote para capturar a mano, la carga del archivo
// que devuelve BancaNet y el visto bueno cuando el archivo no cuadra con el lote.
// El estado manda: un resultado ya Aplicado no se vuelve a tocar.
// Es Tesorería (o un administrador) quien captura y aplica el resultado del banco.
function es_tesoreria() {
	return frappe.user_roles.includes("CxP Tesoreria") || frappe.user_roles.includes("System Manager");
}

// Un resultado nuevo con lote y sin movimientos se le pide al servidor, que lo arma con una línea
// por transferencia y lo guarda; el formulario se muda al documento ya guardado. Pasa al llegar
// desde "Capturar resultado del banco" del lote y también al elegir el lote a mano aquí.
// La bandera evita pedirlo dos veces si el formulario se refresca antes de cambiar de ruta.
function pedir_captura(frm) {
	if (!frm.is_new() || !frm.doc.lote || (frm.doc.movimientos || []).length || frm.__cxp_captura_pedida) return false;
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
	return true;
}

frappe.ui.form.on("Resultado Bancario", {
	lote(frm) {
		// Al elegir el lote a mano en un resultado nuevo: sus transferencias se traen solas, igual
		// que si se hubiera llegado desde el botón del lote.
		if (es_tesoreria()) pedir_captura(frm);
	},

	refresh(frm) {
		if (!es_tesoreria()) return;

		if (pedir_captura(frm)) return;
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
		}

		// Aplicar es lo único que mueve dinero: crea y envía un pago por cada transferencia que el
		// banco marcó con 3. Se puede repetir sin miedo (una transferencia ya Aplicado no se vuelve a
		// pagar), pero se pregunta antes porque los pagos ya enviados sólo se deshacen cancelándolos.
		if (frm.doc.estado !== "Aplicado" && (frm.doc.movimientos || []).some((m) => m.estatus)) {
			frm.add_custom_button(__("Aplicar pagos"), () => frappe.confirm(
				__("Se crearán y enviarán los pagos de las {0} transferencias que el banco aplicó, y las rechazadas quedarán marcadas. ¿Continuar?",
					[frm.doc.num_aplicados || 0]),
				() => frappe.call({
					method: "gode_cxp.banamex.api.aplicar",
					args: { resultado: frm.doc.name },
					freeze: true,
					freeze_message: __("Creando los pagos…"),
					callback: (r) => {
						frm.reload_doc();
						if (!r.message || !r.message.resumen) return;
						frappe.msgprint({
							title: __("Pagos del lote"),
							indicator: r.message.sin_coincidencia || r.message.rechazados ? "orange" : "green",
							message: frappe.utils.escape_html(r.message.resumen),
						});
					},
				})), __("Banco")).addClass("btn-primary");
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
