import frappe


def execute():
	frappe.rename_doc("DocType", "POS Closing Voucher", "POS Closing Entry")
	frappe.rename_doc("DocType", "POS Closing Voucher Details", "POS Closing Entry Detail")
	frappe.rename_doc("DocType", "POS Closing Voucher Taxes", "POS Closing Entry Taxes")
	frappe.rename_doc("DocType", "POS Closing Voucher Invoices", "POS Closing Entry Invoice")
