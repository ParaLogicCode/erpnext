frappe.listview_settings['Sales Order'] = {
	add_fields: [
		"name", "customer_name", "currency", "delivery_date",
		"delivery_status", "billing_status", "status"
	],
	get_indicator: function (doc) {
		// Closed
		if (doc.status === "Closed") {
			return [__("Closed"), "green", "status,=,Closed"];

		// On Hold
		} else if (doc.status === "On Hold") {
			return [__("On Hold"), "orange", "status,=,On Hold"];

		// Completed
		} else if (doc.status === "Completed") {
			return [__("Completed"), "green", "status,=,Completed"];

		// Undelivered
		} else if (doc.delivery_status == "To Deliver") {
			if (frappe.datetime.get_diff(doc.delivery_date) < 0) {
			// Overdue
				return [__("Overdue"), "red", "delivery_date,<,Today|delivery_status,=,To Deliver|docstatus,=,1"];

			// Not Delivered & Not Billed
			} else if (doc.billing_status == "To Bill") {
				return [__("To Deliver and Bill"), "orange", "delivery_status,=,To Deliver|billing_status,=,To Bill|docstatus,=,1"];

			// Billed but not delivered
			} else {
				return [__("To Deliver"), "orange", "delivery_status,=,To Deliver|billing_status,!=,To Bill|docstatus,=,1"];
			}

		// To Bill
		} else if (doc.billing_status == "To Bill") {
			return [__("To Bill"), "orange", "delivery_status,!=,To Deliver|billing_status,=,To Bill|docstatus,=,1"];
		}
	},
	onload: function(listview) {
		erpnext.setup_applies_to_listview_filters(listview);

		const method = "erpnext.selling.doctype.sales_order.sales_order.close_or_unclose_sales_orders";

		listview.page.add_action_item(__("Close"), function() {
			listview.call_for_selected_items(method, {"status": "Closed"});
		});

		listview.page.add_action_item(__("Re-Open"), function() {
			listview.call_for_selected_items(method, {"status": "Submitted"});
		});
	}
};
