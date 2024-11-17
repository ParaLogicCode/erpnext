frappe.provide("erpnext");

erpnext.base_appointment_list_onload = frappe.listview_settings['Appointment'].onload;

frappe.listview_settings['Appointment'].onload = function(listview) {
	erpnext.base_appointment_list_onload(listview);
	erpnext.setup_applies_to_listview_filters(listview);
};
