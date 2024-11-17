# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import cint
from frappe import _
from frappe.desk.notifications import clear_notifications
import functools


def get_excluded_doctypes():
	excluded_doctypes = [
		"Account", "Cost Center", "Budget", "Warehouse",
		"Sales Taxes and Charges Template", "Purchase Taxes and Charges Template",
		"Party Account", "Employee", "BOM",
		"POS Profile", "Mode Of Payment", "Mode of Payment Account",
		"Company", "Bank Account", "Item Tax Template",
		"Item Default Rule", "Customer", "Supplier", "GST Account", "Department",
	]

	for doctypes in frappe.get_hooks("delete_company_transactions_exclude_doctypes"):
		if isinstance(doctypes, str):
			doctypes = [doctypes]

		excluded_doctypes += doctypes

	return excluded_doctypes


@frappe.whitelist()
def delete_company_transactions(company_name):
	frappe.only_for("System Manager")
	doc = frappe.get_doc("Company", company_name)

	if frappe.session.user != doc.owner and frappe.session.user != 'Administrator':
		frappe.throw(_("Transactions can only be deleted by the creator of the Company"),
			frappe.PermissionError)

	delete_bins(company_name)

	excluded_doctypes = get_excluded_doctypes()
	for doctype in frappe.db.sql_list("""select parent from tabDocField where fieldtype='Link' and options='Company'"""):
		if doctype not in excluded_doctypes:
			delete_for_doctype(doctype, company_name)

	# reset company values
	doc.total_monthly_sales = 0
	doc.sales_monthly_history = None
	doc.save()
	# Clear notification counts
	clear_notifications()


def delete_for_doctype(doctype, company_name):
	meta = frappe.get_meta(doctype)
	company_fieldname = meta.get("fields", {"fieldtype": "Link",
		"options": "Company"})[0].fieldname

	if not meta.issingle:
		if not meta.istable:
			# delete communication
			delete_communications(doctype, company_name, company_fieldname)

			# delete children
			for df in meta.get_table_fields():
				frappe.db.sql("""
					delete from `tab{0}`
					where parent in (select name from `tab{1}` where `{2}`=%s)
				""".format(df.options, doctype, company_fieldname), company_name)

		# delete version log
		frappe.db.sql("""
			delete from `tabVersion`
			where ref_doctype=%s and docname in (select name from `tab{0}` where `{1}` = %s)
		""".format(doctype, company_fieldname), (doctype, company_name))

		# delete comment
		frappe.db.sql("""
			delete from `tabComment`
			where reference_doctype=%s and reference_name in (select name from `tab{0}` where `{1}` = %s)
		""".format(doctype, company_fieldname), (doctype, company_name))

		# delete parent
		frappe.db.sql("""delete from `tab{0}`
			where {1}= %s """.format(doctype, company_fieldname), company_name)

		# reset series
		naming_series = meta.get_field("naming_series")
		if naming_series and naming_series.options:
			prefixes = sorted(naming_series.options.split("\n"),
				key=functools.cmp_to_key(lambda a, b: len(b) - len(a)))

			for prefix in prefixes:
				if prefix:
					last = frappe.db.sql("""select max(name) from `tab{0}`
						where name like %s""".format(doctype), prefix + "%")
					if last and last[0][0]:
						last = cint(last[0][0].replace(prefix, ""))
					else:
						last = 0

					frappe.db.sql("""update tabSeries set current = %s
						where name=%s""", (last, prefix))


def delete_bins(company_name):
	frappe.db.sql("""delete from tabBin where warehouse in
			(select name from tabWarehouse where company=%s)""", company_name)


def delete_communications(doctype, company_name, company_fieldname):

	refrence_docs = frappe.get_all(doctype, filters={company_fieldname:company_name})
	reference_doctype_names = [r.name for r in refrence_docs]

	communications = frappe.get_all("Communication", filters={"reference_doctype":doctype,"reference_name":["in",reference_doctype_names]})
	communication_names = [c.name for c in communications]

	frappe.delete_doc("Communication", communication_names, ignore_permissions=True)
