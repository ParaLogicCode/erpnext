import frappe


def execute():
	frappe.delete_doc_if_exists("Print Format", "Detailed Tax Invoice")
	frappe.delete_doc_if_exists("Print Format", "Simplified Tax Invoice")
	frappe.delete_doc_if_exists("Print Format", "Tax Invoice")

	country = frappe.get_system_settings('country')
	if country != "United Arab Emirates":
		return

	frappe.delete_doc_if_exists("Custom Field", "Item-tax_code")

	frappe.delete_doc_if_exists("Custom Field", "Quotation Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Quotation Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Quotation Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Quotation Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Sales Order Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Sales Order Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Sales Order Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Sales Order Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Delivery Note Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Delivery Note Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Delivery Note Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Delivery Note Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Sales Invoice Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Sales Invoice Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Sales Invoice Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Sales Invoice Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Supplier Quotation Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Supplier Quotation Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Supplier Quotation Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Supplier Quotation Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Purchase Order Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Order Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Order Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Order Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Purchase Receipt Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Receipt Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Receipt Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Receipt Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Purchase Invoice Item-tax_code")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Invoice Item-tax_rate")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Invoice Item-tax_amount")
	frappe.delete_doc_if_exists("Custom Field", "Purchase Invoice Item-total_amount")

	frappe.delete_doc_if_exists("Custom Field", "Sales Invoice-reverse_charge_applicable")

	frappe.reload_doc("regional", "report", "uae_vat_201")

	from erpnext.regional.united_arab_emirates.setup import setup
	setup()
