import frappe


def execute():
	if frappe.db.exists("DocType", "POS Closing Entry Detail"):
		frappe.rename_doc("DocType", "POS Closing Entry Detail", "POS Closing Entry Reconciliation")

	frappe.delete_doc_if_exists("DocType", "POS Closing Entry Invoice")
