import frappe


def execute():
	mreqs = frappe.get_all("Material Request", filters={"status": "Stopped"}, pluck="name")
	for name in mreqs:
		doc = frappe.get_doc("Material Request", name)
		doc.set_completion_status(update=True, update_modified=False)
		doc.clear_cache()

	projects = frappe.db.sql_list("""
		select distinct p.name
		from `tabProject` p
		inner join `tabMaterial Request` mr on mr.project = p.name
		where mr.docstatus = 1
	""")

	for name in projects:
		doc = frappe.get_doc("Project", name)
		doc.set_billing_and_delivery_status(update=True, update_modified=False)
		doc.clear_cache()
