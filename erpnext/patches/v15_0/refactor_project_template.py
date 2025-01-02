import frappe


def execute():
	all_items = frappe.db.sql("""
		select * from `tabProject Template Item` where parentfield = 'applicable_items'
	""", as_dict=True)

	template_items = {}
	for d in all_items:
		del d['parentfield']
		del d['idx']
		del d['name']
		template_items.setdefault(d.parent, []).append(d)

	for project_template, items in template_items.items():
		print(project_template, len(items))
		doc = frappe.get_doc("Project Template", project_template)
		for d in items:
			if d.get("use_stock_entry"):
				row = doc.append("consumable_items", d)
				row.db_insert()
			else:
				row = doc.append("sales_items", d)
				row.db_insert()
