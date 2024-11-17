frappe.provide("erpnext");

erpnext.base_customer_feedback_list_onload = frappe.listview_settings['Customer Feedback'].onload;

frappe.listview_settings['Customer Feedback'].onload = function(listview) {
	erpnext.base_customer_feedback_list_onload(listview);
	erpnext.setup_applies_to_listview_filters(listview);
};
