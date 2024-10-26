import frappe


def execute():
	frappe.reload_doctype("Work Order Item")
	frappe.db.sql("""
		update `tabWork Order Item` woi
		inner join `tabItem` item on item.name = woi.item_code
		set woi.has_batch_no = item.has_batch_no
	""")
