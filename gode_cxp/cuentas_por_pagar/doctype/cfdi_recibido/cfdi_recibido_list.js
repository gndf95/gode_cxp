// Bandeja de CFDI: botón para subir XML sueltos o un ZIP, y color por estado.
frappe.listview_settings["CFDI Recibido"] = {
	add_fields: ["estado"],
	onload(listview) {
		listview.page.add_inner_button(__("Subir XML o ZIP"), () => cxp_subir_cfdi(listview));
	},
	get_indicator(doc) {
		const colores = {
			Nuevo: "blue",
			"Con factura": "green",
			Ajeno: "orange",
			Ignorado: "gray",
			Error: "red",
		};
		return [__(doc.estado), colores[doc.estado] || "gray", "estado,=," + doc.estado];
	},
};

function cxp_subir_cfdi(listview) {
	// Este FileUploader no avisa cuando termina TODA la subida (no tiene on_complete: sólo llama a
	// on_success una vez por archivo), así que se juntan las URL y se procesan cuando pasa medio
	// segundo sin que llegue ninguna más.
	const urls = [];
	let temporizador = null;
	new frappe.ui.FileUploader({
		dialog_title: __("Subir facturas"),
		upload_notes: __("XML de CFDI, o ZIP con el XML y el PDF del mismo nombre"),
		allow_multiple: true,
		make_attachments_public: false,
		restrictions: { allowed_file_types: [".xml", ".zip"] },
		on_success: (archivo) => {
			if (archivo && archivo.file_url) {
				urls.push(archivo.file_url);
			}
			clearTimeout(temporizador);
			temporizador = setTimeout(() => cxp_procesar_carga(listview, urls), 500);
		},
	});
}

function cxp_procesar_carga(listview, file_urls) {
	if (!file_urls.length) {
		return;
	}
	frappe.call({
		method: "gode_cxp.facturas.api.procesar_archivos",
		args: { file_urls: file_urls },
		freeze: true,
		freeze_message: __("Leyendo CFDI…"),
		callback: (r) => {
			const d = r.message;
			if (!d) {
				return;
			}
			let mensaje = __("Nuevos: {0} · Duplicados: {1} · Ajenos: {2} · Facturas creadas: {3} · Errores: {4}", [
				d.nuevos.length,
				d.duplicados.length,
				d.ajenos.length,
				d.facturas.length,
				d.errores.length,
			]);
			if (d.errores.length) {
				// escape_html: el nombre del archivo lo pone quien sube, no se inyecta tal cual.
				mensaje +=
					"<br><br>" +
					d.errores
						.map(
							(e) =>
								frappe.utils.escape_html(e.archivo) +
								": " +
								frappe.utils.escape_html(e.error)
						)
						.join("<br>");
			}
			frappe.msgprint({ title: __("Resultado de la carga"), message: mensaje });
			listview.refresh();
		},
	});
}
