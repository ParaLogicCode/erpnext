frappe.listview_settings['Purchase Receipt'] = {
	add_fields: [
		"supplier", "supplier_name",
		"is_return", "status", "billing_status",
	],

	get_indicator: function(doc) {
		// Return
		if (cint(doc.is_return)) {
			return [__("Return"), "grey", "is_return,=,Yes"];

		// Closed
		} else if (doc.status === "Closed") {
			return [__("Closed"), "green", "status,=,Closed"];

		// To Bill
		} else if (doc.billing_status == "To Bill") {
			return [__("To Bill"), "orange", "billing_status,=,To Bill|docstatus,=,1"];

		// Completed
		} else if (doc.billing_status != "To Bill") {
			return [__("Completed"), "green", "billing_status,!=,To Bill|docstatus,=,1"];
		}
	},

	onload: function(listview) {
		erpnext.setup_applies_to_listview_filters(listview);
	},
};
