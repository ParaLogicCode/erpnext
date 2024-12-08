import frappe


def execute():
	# Sales Orders
	mreqs = frappe.get_all("Material Request", pluck="name", filters={"docstatus": ["<", 2]})
	print(f"Updating {len(mreqs)} Material Request statuses")
	for name in mreqs:
		doc = frappe.get_doc("Material Request", name)
		doc.set_completion_status()

		doc.db_set({
			"order_status": doc.order_status,
			"receipt_status": doc.receipt_status,
		}, update_modified=False)

		doc.clear_cache()

	# Cancelled Status
	frappe.db.sql(f"""
		update `tabMaterial Request`
		set status = 'Cancelled', order_status = 'Not Applicable', receipt_status = 'Not Applicable'
		where docstatus = 2
	""")
