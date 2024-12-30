# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def setup(company=None, patch=True):
	make_custom_fields()
	add_custom_roles_for_reports()


def make_custom_fields():
	invoice_fields = [
		dict(
			fieldname="vat_section",
			label="VAT Details",
			fieldtype="Section Break",
			insert_after="language",
			print_hide=1,
			collapsible=0,
		),
		dict(
			fieldname="permit_no",
			label="Permit Number",
			fieldtype="Data",
			insert_after="vat_section",
			print_hide=1,
		),
	]

	purchase_invoice_fields = [
		dict(
			fieldname="company_trn",
			label="Company TRN",
			fieldtype="Read Only",
			insert_after="shipping_address",
			fetch_from="company.tax_id",
			print_hide=1,
		),
		dict(
			fieldname="supplier_name_in_arabic",
			label="Supplier Name in Arabic",
			fieldtype="Read Only",
			insert_after="supplier_name",
			fetch_from="supplier.supplier_name_in_arabic",
			print_hide=1,
		),
		dict(
			fieldname="reverse_charge",
			label="Reverse Charge Applicable",
			fieldtype="Select",
			insert_after="permit_no",
			print_hide=1,
			options="Y\nN",
			default="N",
		),
		dict(
			fieldname="recoverable_reverse_charge",
			label="Recoverable Reverse Charge (Percentage)",
			insert_after="reverse_charge",
			fieldtype="Percent",
			print_hide=1,
			depends_on="eval:doc.reverse_charge=='Y'",
			default="100.000",
		),
	]

	sales_invoice_fields = [
		dict(
			fieldname="company_trn",
			label="Company TRN",
			fieldtype="Read Only",
			insert_after="company_address",
			fetch_from="company.tax_id",
			print_hide=1,
		),
		dict(
			fieldname="customer_name_in_arabic",
			label="Customer Name in Arabic",
			fieldtype="Read Only",
			insert_after="customer_name",
			fetch_from="customer.customer_name_in_arabic",
			print_hide=1,
		),
		dict(
			fieldname="vat_emirate",
			label="VAT Emirate",
			insert_after="permit_no",
			fieldtype="Select",
			options="\nAbu Dhabi\nAjman\nDubai\nFujairah\nRas Al Khaimah\nSharjah\nUmm Al Quwain",
			fetch_from="company_address.emirate",
		),
		dict(
			fieldname="tourist_tax_return",
			label="Tax Refund provided to Tourists (AED)",
			insert_after="vat_emirate",
			fieldtype="Currency",
			print_hide=1,
			default="0",
		),
	]

	is_zero_rated = dict(
		fieldname="is_zero_rated",
		label="Is Zero Rated",
		fieldtype="Check",
		insert_after="total_amount",
		print_hide=1,
	)
	is_exempt = dict(
		fieldname="is_exempt",
		label="Is Exempt",
		fieldtype="Check",
		insert_after="is_zero_rated",
		print_hide=1,
	)

	custom_fields = {
		'Customer': [
			dict(
				fieldname="customer_name_in_arabic",
				label="Customer Name in Arabic",
				fieldtype="Data",
				insert_after="customer_name",
			),
		],
		'Supplier': [
			dict(
				fieldname="supplier_name_in_arabic",
				label="Supplier Name in Arabic",
				fieldtype="Data",
				insert_after="supplier_name",
			),
		],
		"Address": [
			dict(
				fieldname="emirate",
				label="Emirate",
				fieldtype="Select",
				insert_after="state",
				options="\nAbu Dhabi\nAjman\nDubai\nFujairah\nRas Al Khaimah\nSharjah\nUmm Al Quwain",
			)
		],
		"Purchase Invoice": purchase_invoice_fields + invoice_fields,
		"Purchase Order": purchase_invoice_fields + invoice_fields,
		"Purchase Receipt": purchase_invoice_fields + invoice_fields,
		"Sales Invoice": sales_invoice_fields + invoice_fields,
		"Sales Order": sales_invoice_fields + invoice_fields,
		"Delivery Note": sales_invoice_fields + invoice_fields,
		"Sales Invoice Item": [is_zero_rated, is_exempt],
	}

	create_custom_fields(custom_fields)


def add_custom_roles_for_reports():
	"""Add Access Control to UAE VAT 201."""
	if not frappe.db.get_value("Custom Role", dict(report="UAE VAT 201")):
		frappe.get_doc(
			dict(
				doctype="Custom Role",
				report="UAE VAT 201",
				roles=[dict(role="Accounts User"), dict(role="Accounts Manager"), dict(role="Auditor")],
			)
		).insert()
