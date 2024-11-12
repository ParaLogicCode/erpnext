frappe.provide("erpnext");

erpnext.base_opportunity_list_onload = frappe.listview_settings['Opportunity'].onload;

frappe.listview_settings['Opportunity'].onload = function(listview) {
	erpnext.base_opportunity_list_onload(listview);
	erpnext.setup_applies_to_listview_filters(listview);
};
