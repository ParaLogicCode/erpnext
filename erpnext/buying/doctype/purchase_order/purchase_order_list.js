frappe.listview_settings['Purchase Order'] = {
	add_fields: [
		"supplier", "supplier_name",
		"receipt_status", "billing_status", "status"
	],

	get_indicator: function (doc) {
		// Closed
		if (doc.status === "Closed") {
			return [__("Closed"), "green", "status,=,Closed"];

		// On Hold
		} else if (doc.status === "On Hold") {
			return [__("On Hold"), "orange", "status,=,On Hold"];

		// Delivered by Supplier
		} else if (doc.status === "Delivered") {
			return [__("Delivered"), "green", "status,=,Delivered"];

		// Completed
		} else if (doc.status === "Completed") {
			return [__("Completed"), "green", "status,=,Completed"];

		// To Receive
		} else if (doc.receipt_status == "To Receive") {
			// Not Received and Not Billed
			if (doc.billing_status == "To Bill") {
				return [__("To Receive and Bill"), "orange",
					"receipt_status,=,To Receive|billing_status,=,To Bill|docstatus,=,1"];

			// Billed but not received
			} else {
				return [__("To Receive"), "orange",
					"receipt_status,=,To Receive|billing_status,!=,To Bill|docstatus,=,1"];
			}

		// To Bill
		} else if (doc.receipt_status != "To Receive" && doc.billing_status == "To Bill") {
			return [__("To Bill"), "orange",
				"receipt_status,!=,To Receive|billing_status,=,To Bill|docstatus,=,1"];
		}
	},

	onload: function (listview) {
		erpnext.setup_applies_to_listview_filters(listview);

		const method = "erpnext.buying.doctype.purchase_order.purchase_order.close_or_unclose_purchase_orders";

		listview.page.add_action_item(__("Close"), function () {
			listview.call_for_selected_items(method, { "status": "Closed" });
		});

		listview.page.add_action_item(__("Re-Open"), function () {
			listview.call_for_selected_items(method, { "status": "Submitted" });
		});
	}
};
