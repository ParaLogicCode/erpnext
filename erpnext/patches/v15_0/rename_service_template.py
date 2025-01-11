import frappe
from frappe.model.utils.rename_field import rename_field
from frappe.utils.fixtures import sync_fixtures


def execute():
	# Rename DocTypes
	if frappe.db.exists("DocType", "Project Template"):
		frappe.rename_doc("DocType", "Project Template", "Service Template", force=True)
	if frappe.db.exists("DocType", "Project Template Category"):
		frappe.rename_doc("DocType", "Project Template Category", "Service Template Category", force=True)
	if frappe.db.exists("DocType", "Project Template Item"):
		frappe.rename_doc("DocType", "Project Template Item", "Service Template Item", force=True)
	if frappe.db.exists("DocType", "Project Template Consumable"):
		frappe.rename_doc("DocType", "Project Template Consumable", "Service Template Consumable", force=True)
	if frappe.db.exists("DocType", "Project Template Item Group"):
		frappe.rename_doc("DocType", "Project Template Item Group", "Service Template Item Group", force=True)
	if frappe.db.exists("DocType", "Project Template Task"):
		frappe.rename_doc("DocType", "Project Template Task", "Service Template Task", force=True)
	if frappe.db.exists("DocType", "Project Template Detail"):
		frappe.rename_doc("DocType", "Project Template Detail", "Project Service Template", force=True)

	# Reload DocTypes
	frappe.reload_doc("projects", "doctype", "service_template")
	frappe.reload_doc("projects", "doctype", "service_template_category")
	frappe.reload_doc("projects", "doctype", "service_template_item")
	frappe.reload_doc("projects", "doctype", "service_template_consumable")
	frappe.reload_doc("projects", "doctype", "service_template_item_group")
	frappe.reload_doc("projects", "doctype", "service_template_task")
	frappe.reload_doc("projects", "doctype", "project_service_template")

	frappe.reload_doctype("Project")
	frappe.reload_doctype("Quotation Item")
	frappe.reload_doctype("Sales Order Item")
	frappe.reload_doctype("Material Request Item")
	frappe.reload_doctype("Task")
	frappe.reload_doctype("Maintenance Schedule Detail")
	frappe.reload_doctype("Projects Settings")

	# Rename fields
	rename_field("Service Template", "project_template_code", "service_template_code")
	rename_field("Service Template", "project_template_name", "service_template_name")
	rename_field("Service Template", "project_template_category", "service_template_category")
	rename_field("Service Template", "next_project_template", "next_service_template")

	rename_field("Service Template Category", "project_template_category", "template_category")

	rename_field("Project", "project_templates", "service_templates")

	rename_field("Project Service Template", "project_template", "service_template")
	rename_field("Project Service Template", "project_template_name", "service_template_name")

	rename_field("Quotation Item", "project_template", "service_template")

	rename_field("Sales Order Item", "project_template", "service_template")
	rename_field("Sales Order Item", "project_template_detail", "service_template_detail")

	rename_field("Material Request Item", "project_template", "service_template")
	rename_field("Material Request Item", "project_template_detail", "service_template_detail")

	rename_field("Task", "project_template", "service_template")
	rename_field("Task", "project_template_detail", "service_template_detail")

	rename_field("Maintenance Schedule Detail", "project_template", "service_template")
	rename_field("Maintenance Schedule Detail", "project_template_name", "service_template_name")

	rename_field("Projects Settings", "auto_schedule_next_project_templates", "auto_schedule_next_service_templates")

	sync_fixtures(app="erpnext")
	rename_field("Appointment", "project_template", "service_template")
	rename_field("Appointment", "project_template_name", "service_template_name")

	frappe.delete_doc_if_exists("Custom Field", "Appointment-sec_project_template")
	frappe.delete_doc_if_exists("Custom Field", "Appointment-project_template")
	frappe.delete_doc_if_exists("Custom Field", "Appointment-cb1_project_template")
	frappe.delete_doc_if_exists("Custom Field", "Appointment-project_template_name")
