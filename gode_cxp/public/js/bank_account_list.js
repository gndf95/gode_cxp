// Acciones del alta (pre-registro) de cuentas de proveedor en BancaNet, sobre la lista de cuentas
// bancarias. Banamex no deja pagar a una cuenta que no está dada de alta en el contrato: el banco
// entrega una plantilla de Excel con macros, la app produce las filas y lee la respuesta.
//
// Se EXTIENDE listview_settings["Bank Account"] en vez de reasignarlo: hoy ni Frappe ni ERPNext lo
// definen, pero si mañana lo hacen (los dos archivos llegan pegados en el mismo __list_js) aquí no
// se perdería nada.
frappe.listview_settings["Bank Account"] = frappe.listview_settings["Bank Account"] || {};
const cxp_ajustes_ba = frappe.listview_settings["Bank Account"];
const cxp_onload_previo_ba = cxp_ajustes_ba.onload;
const cxp_indicador_previo_ba = cxp_ajustes_ba.get_indicator;
// El estado del alta se pinta en la lista y se filtra desde las acciones: tiene que venir en las
// filas. `verificada` y `disabled` los necesita el indicador (no son columnas de la lista).
cxp_ajustes_ba.add_fields = (cxp_ajustes_ba.add_fields || []).concat(
	["party_type", "estado_preregistro", "verificada", "disabled"]);

// De un vistazo: si a esta cuenta ya se le puede pagar. Sólo se pronuncia sobre cuentas de proveedor
// activas; para todo lo demás (cuentas de la empresa, cuentas deshabilitadas) devuelve undefined y
// Frappe sigue con su lógica de siempre —Habilitada / Deshabilitada—, que es lo que hacía hasta hoy
// (frappe/public/js/frappe/model/indicator.js: settings.get_indicator sólo manda si devuelve algo).
cxp_ajustes_ba.get_indicator = function (doc) {
	const previo = cxp_indicador_previo_ba && cxp_indicador_previo_ba.call(cxp_ajustes_ba, doc);
	if (previo) return previo;
	if (doc.party_type !== "Supplier" || doc.disabled) return;
	if (!doc.verificada) return [__("Sin verificar"), "gray", "verificada,=,0"];
	if (doc.estado_preregistro === "Registrada") {
		return [__("Lista para pagar"), "green", "estado_preregistro,=,Registrada"];
	}
	return [__("Falta el alta en el banco"), "orange", "estado_preregistro,!=,Registrada"];
};

cxp_ajustes_ba.onload = function (listview) {
	if (cxp_onload_previo_ba) cxp_onload_previo_ba.call(cxp_ajustes_ba, listview);
	if (!(frappe.user_roles.includes("CxP Tesoreria") || frappe.user_roles.includes("System Manager"))) return;

	listview.page.add_action_item(__("Descargar pre-registro para BancaNet"), () => {
		// Sin selección se bajan TODAS las que faltan por registrar (el caso normal); con selección,
		// sólo ésas (por ejemplo para volver a mandar una que el banco rechazó).
		const nombres = listview.get_checked_items().map((d) => d.name);
		frappe.call({
			method: "gode_cxp.pagos.api.descargar_preregistro",
			args: nombres.length ? { cuentas: JSON.stringify(nombres) } : {},
			freeze: true,
			callback: (r) => {
				if (!r.message) return;
				listview.refresh();
				// El enlace va en un msgprint: la descarga se decide dentro de un callback y el
				// navegador bloquearía una ventana emergente.
				frappe.msgprint({
					title: __("Pre-registro listo ({0} cuentas)", [r.message.cuentas]),
					indicator: "green",
					message: `<p>${__("Pega las filas en la plantilla del banco (hoja LOIncorOP) y genera el archivo con su macro.")}</p>
						<p><a href="${encodeURI(r.message.file_url)}" download>${frappe.utils.escape_html(r.message.nombre)}</a></p>`,
				});
			},
		});
	});

	listview.page.add_action_item(__("Cargar respuesta del pre-registro"), () => {
		const d = new frappe.ui.Dialog({
			title: __("Respuesta del banco"),
			fields: [
				{ fieldname: "archivo", fieldtype: "Attach", label: __("Archivo abcMasivo… del banco"), reqd: 1 },
			],
			primary_action_label: __("Aplicar"),
			primary_action(valores) {
				frappe.call({
					method: "gode_cxp.pagos.api.aplicar_respuesta_preregistro",
					args: { file_url: valores.archivo },
					freeze: true,
					callback: (r) => {
						d.hide();
						listview.refresh();
						if (!r.message) return;
						// Cada renglón que no se aplicó trae los últimos 4 dígitos de la cuenta (el
						// número completo no sale del servidor) y el motivo por el que no se aplicó.
						const sin = (r.message.sin_coincidencia || []).map((s) => `…${s.cuenta}: ${s.motivo}`);
						frappe.msgprint({
							title: __("Respuesta aplicada"),
							indicator: sin.length || r.message.rechazadas ? "orange" : "green",
							message: `<p>${__("Registradas: {0} · Rechazadas: {1}", [r.message.registradas, r.message.rechazadas])}</p>`
								+ (sin.length
									? `<p>${__("Renglones que no se aplicaron:")}</p><ul><li>`
										+ sin.map((s) => frappe.utils.escape_html(s)).join("</li><li>")
										+ "</li></ul>"
									: ""),
						});
					},
				});
			},
		});
		d.show();
	});

	listview.page.add_action_item(__("Marcar como ya registradas"), () => {
		const nombres = listview.get_checked_items().map((d) => d.name);
		if (!nombres.length) {
			frappe.msgprint(__("Selecciona las cuentas que ya estaban dadas de alta en BancaNet."));
			return;
		}
		frappe.confirm(
			__("¿Confirmas que estas {0} cuentas ya están dadas de alta en BancaNet? Queda escrito que lo marcaste a mano.", [nombres.length]),
			() => frappe.call({
				method: "gode_cxp.pagos.api.marcar_registrada",
				args: { cuentas: JSON.stringify(nombres) },
				freeze: true,
				callback: () => listview.refresh(),
			}),
		);
	});
};
