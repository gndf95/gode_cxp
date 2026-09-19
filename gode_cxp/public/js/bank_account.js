// Botón para que Tesorería verifique la cuenta de un proveedor antes de que entre a un lote.
frappe.ui.form.on("Bank Account", {
	refresh(frm) {
		if (frm.doc.party_type === "Supplier" && frm.doc.clabe && !frm.doc.verificada && !frm.is_new() && !frm.is_dirty()
			&& (frappe.user_roles.includes("CxP Tesoreria") || frappe.user_roles.includes("System Manager"))) {
			frm.add_custom_button(__("Verificar cuenta"), () => {
				// El botón se dibuja en refresh, pero el formulario se puede ensuciar después sin
				// que refresh vuelva a correr: lo que se verifica es lo guardado, no lo de pantalla.
				if (frm.is_dirty()) {
					frappe.msgprint(__("Guarda primero los cambios: la verificación se hace sobre lo que está guardado."));
					return;
				}
				frappe.confirm(__("¿Confirmas que la CLABE {0} y el beneficiario '{1}' son correctos?", [frm.doc.clabe, frm.doc.nombre_tef]), () => {
					frappe.call({ method: "gode_cxp.pagos.cuentas_bancarias.verificar_cuenta", args: { name: frm.doc.name }, callback: () => frm.reload_doc() });
				});
			});
		}
	},
});
