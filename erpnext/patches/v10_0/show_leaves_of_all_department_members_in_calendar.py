import frappe

def execute():
	frappe.reload_doc("hr", "doctype", "hr_settings")
	frappe.db.set_single_value("HR Settings", "show_leaves_of_all_department_members_in_calendar", 1)