import frappe
from frappe.utils import cint


def execute():
	old = frappe.db.get_value("Singles", filters={
		"doctype": "Accounts Settings", "field": "restrict_sales_tax_invoice_without_tax_id"
	}, fieldname="value", order_by=None)

	if cint(old):
		frappe.db.set_single_value("Accounts Settings",
			"validate_sales_invoice_tax_id", "Mandatory", update_modified=False)
	elif old:
		frappe.db.set_single_value("Accounts Settings",
			"validate_sales_invoice_tax_id", None, update_modified=False)
