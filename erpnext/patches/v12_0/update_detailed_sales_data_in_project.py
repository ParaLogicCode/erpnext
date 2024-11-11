import frappe


def execute():
	frappe.reload_doc("projects", "doctype", "project")

	update_fields = [
		'part_sales_amount',
		'lubricant_sales_amount',
		'labour_sales_amount',
		'sublet_sales_amount',
	]

	projects = frappe.get_all("Project")
	projects = [d.name for d in projects]

	total = len(projects)
	for i, project in enumerate(projects):
		print("Project {0}/{1}".format(i+1, total))
		doc = frappe.get_doc("Project", project)
		doc.set_sales_amount()

		updated_values = {}
		for fn in update_fields:
			updated_values[fn] = doc.get(fn)

		doc.db_set(updated_values, update_modified=False)
		doc.clear_cache()
