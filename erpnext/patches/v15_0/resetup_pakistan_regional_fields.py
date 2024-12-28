import frappe


def execute():
	country = frappe.get_system_settings('country')
	if country != "Pakistan":
		return

	from erpnext.regional.pakistan.setup import setup
	setup()
