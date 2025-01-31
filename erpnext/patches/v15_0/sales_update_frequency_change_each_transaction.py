import frappe


def execute():
	old = frappe.db.get_value("Singles", filters={
		"doctype": "Selling Settings", "field": "sales_update_frequency"
	}, fieldname="value", order_by=None)

	if old == "Each Transaction":
		frappe.db.set_single_value("Selling Settings",
			"sales_update_frequency", "Daily", update_modified=False)
