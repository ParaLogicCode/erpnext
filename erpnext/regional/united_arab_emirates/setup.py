# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


# import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from erpnext.setup.setup_wizard.operations.taxes_setup import create_sales_tax


def setup(company=None, patch=True):
	make_custom_fields()
	if company:
		create_sales_tax(company)


def make_custom_fields():
	invoice_fields = [
		dict(fieldname='vat_section', label='VAT Details', fieldtype='Section Break',
			insert_after='group_same_items', print_hide=1, collapsible=1),
		dict(fieldname='permit_no', label='Permit Number',
			fieldtype='Data', insert_after='vat_section', print_hide=1),
		dict(fieldname='reverse_charge_applicable', label='Reverse Charge Applicable',
			fieldtype='Select', insert_after='permit_no', print_hide=1,
			options='Y\nN', default='N')
	]

	purchase_invoice_fields = [
		dict(fieldname='company_trn', label='Company TRN',
			fieldtype='Read Only', insert_after='shipping_address',
			fetch_from='company.tax_id', print_hide=1),
		dict(fieldname='supplier_name_in_arabic', label='Supplier Name in Arabic',
			fieldtype='Read Only', insert_after='supplier_name',
			fetch_from='supplier.supplier_name_in_arabic', print_hide=1)
	]

	sales_invoice_fields = [
		dict(fieldname='company_trn', label='Company TRN',
			fieldtype='Read Only', insert_after='company_address',
			fetch_from='company.tax_id', print_hide=1),
		dict(fieldname='customer_name_in_arabic', label='Customer Name in Arabic',
			fieldtype='Read Only', insert_after='customer_name',
			fetch_from='customer.customer_name_in_arabic', print_hide=1),
	]

	custom_fields = {
		'Customer': [
			dict(fieldname='customer_name_in_arabic', label='Customer Name in Arabic',
				fieldtype='Data', insert_after='customer_name'),
		],
		'Supplier': [
			dict(fieldname='supplier_name_in_arabic', label='Supplier Name in Arabic',
				fieldtype='Data', insert_after='supplier_name'),
		],
		'Purchase Invoice': purchase_invoice_fields + invoice_fields,
		'Purchase Order': purchase_invoice_fields + invoice_fields,
		'Purchase Receipt': purchase_invoice_fields + invoice_fields,
		'Sales Invoice': sales_invoice_fields + invoice_fields,
		'Sales Order': sales_invoice_fields + invoice_fields,
		'Delivery Note': sales_invoice_fields + invoice_fields,
	}

	create_custom_fields(custom_fields)
