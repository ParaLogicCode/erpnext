import frappe
from frappe.installer import install_app
from frappe.core.doctype.installed_applications.installed_applications import get_installed_app_order, update_installed_apps_order
from frappe.modules.patch_handler import get_patches_from_app, PatchType


def execute():
	if "Vehicles" in frappe.get_active_domains():
		if "automotive" not in frappe.get_installed_apps():
			if frappe.db.table_exists("Project Workshop"):
				frappe.rename_doc('DocType', 'Project Workshop', 'Vehicle Workshop', force=True)

			install_app("automotive", set_as_patched=False, force=True)
			patches = get_patches_from_app("automotive", PatchType.pre_model_sync)
			frappe.flags.final_patches += patches
	else:
		remove_automotive_docs()

	app_order = get_installed_app_order()
	erpnext_index = app_order.index("erpnext")
	app_order.remove("automotive")
	app_order.insert(erpnext_index + 1, "automotive")
	update_installed_apps_order(app_order)

	frappe.db.sql("update `tabRole` set restrict_to_domain = null where restrict_to_domain = 'Vehicles'")
	frappe.db.sql("delete from `tabHas Domain` where domain = 'Vehicles'")
	frappe.delete_doc_if_exists("Domain", "Vehicles", force=True)


def remove_automotive_docs():
	dts_to_delete = [
		'Vehicle Receipt',
		'Vehicle Invoice',
		'Vehicle Delivery',
		'Vehicle Quotation',
		'Vehicle Booking Order',
		'Vehicle Gate Pass',
		'Vehicle Panel',
		'Vehicle Panel Side',
		'Vehicle Panel Job',
		'Vehicle Service Receipt',
		'Vehicle Number Plate Delivery',
		'Vehicle Transfer Letter',
		'Vehicle Registration Order',
		'Vehicle Registration Order Payment',
		'Vehicle Movement',
		'Vehicle Invoice Movement',
		'Vehicle Invoice Movement Detail',
		'Vehicle Registration Receipt',
		'Vehicle Allocation Creation Tool',
		'Vehicle Allocation Creation Detail',
		'Vehicle Allocation',
		'Vehicle Allocation Period',
		'Vehicle Withholding Tax Rule',
		'Vehicle Number Plate Receipt',
		'Vehicle Pricing Component',
		'Vehicles Settings',
		'Vehicle Booking Payment',
		'Vehicle Invoice Delivery',
		'Vehicle Invoice Document',
		'Project Panel Detail',
		'Vehicle Registration Component',
		'Vehicle Booking Payment Detail',
		'Vehicle Invoice Document Template',
		'Vehicle Pricing Price List',
		'Vehicle Checklist Item',
		'Vehicle Number Plate Receipt Detail',
		'Vehicle Withholding Tax Engine Capacity',
		'Project Workshop',
	]

	reports_to_delete = [
		'Vehicle Appointment Sheet',
		'Vehicle Service Feedback',
		'Vehicle Booking Deposit Summary',
		'Vehicle Booking Details',
		'Vehicle Booking Analytics',
		'Vehicle Booking Summary',
		'Vehicle Allocation Register',
		'Vehicle Registration Register',
		'Vehicle Stock',
		'Vehicle Tracking Sheet',
		'Vehicle Service Summary',
		'Vehicle Maintenance Schedule',
		'Vehicle Sales Opportunities'
	]

	pages_to_delete = ['workshop-cp']

	workspaces_to_delete = ['Vehicles']

	charts_to_delete = [
		'Vehicle Delivery Timeline',
		'Vehicle Booking Timeline',
		'Booking Status Summary',
		'Top Vehicle Models'
	]

	custom_fields = [
		'Appointment Type-validate_duplicate_appointment'
	]

	for name in dts_to_delete:
		frappe.delete_doc("DocType", name, ignore_missing=True, delete_permanently=True)
	for name in reports_to_delete:
		frappe.delete_doc("Report", name, ignore_missing=True, delete_permanently=True)
	for name in pages_to_delete:
		frappe.delete_doc("Page", name, ignore_missing=True, delete_permanently=True)
	for name in workspaces_to_delete:
		frappe.delete_doc("Workspace", name, ignore_missing=True, delete_permanently=True)
	for name in charts_to_delete:
		frappe.delete_doc("Dashboard Chart", name, ignore_missing=True, delete_permanently=True)
	for name in custom_fields:
		frappe.delete_doc("Custom Field", name, ignore_missing=True, delete_permanently=True)
