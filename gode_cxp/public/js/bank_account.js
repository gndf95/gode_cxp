// Botón para que Tesorería verifique la cuenta de un proveedor antes de que entre a un lote.
frappe.ui.form.on("Bank Account", {
	refresh(frm) {
		if (frm.doc.party_type === "Supplier" && frm.doc.clabe && !frm.doc.verificada && !frm.is_new() && !frm.is_dirty()
			&& (frappe.user_roles.includes("CxP Tesoreria") || frappe.user_roles.includes("System Manager"))) {
			frm.add_custom_button(__("Verificar cuenta"), () => {
				frappe.confirm(__("¿Confirmas que la CLABE {0} y el beneficiario '{1}' son correctos?", [frm.doc.clabe, frm.doc.nombre_tef]), () => {
					frappe.call({ method: "gode_cxp.pagos.cuentas_bancarias.verificar_cuenta", args: { name: frm.doc.name }, callback: () => frm.reload_doc() });
				});
			});
		}
	},
});
