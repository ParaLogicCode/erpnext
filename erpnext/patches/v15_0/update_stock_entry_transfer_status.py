import frappe


def execute():
	stes = frappe.get_all("Stock Entry", filters={
		"purpose": ["in", ["Material Transfer", "Send to Warehouse", "Receive at Warehouse"]]
	}, pluck="name")

	for name in stes:
		doc = frappe.get_doc("Stock Entry", name)
		doc.set_transferred_status(update=True, update_modified=False)
		doc.clear_cache()
