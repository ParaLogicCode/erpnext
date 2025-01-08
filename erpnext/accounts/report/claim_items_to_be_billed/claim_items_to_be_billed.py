# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate
from erpnext.accounts.report.sales_items_to_be_billed.sales_items_to_be_billed import ItemsToBeBilled
import json


class ClaimItemsToBeBilled(ItemsToBeBilled):
	def sort_data(self):
		if self.filters.date_type == "Project Date":
			self.data = sorted(self.data, key=lambda d: (getdate(d.project_date), d.project))
		else:
			super().sort_data()

	def prepare_data(self):
		for d in self.data:
			if d.get("claim_customer") and d.get("discount_percentage"):
				d.amount = d.amount_before_discount

		super().prepare_data()

	def get_select_fields_and_joins(self, doctype):
		select_fields, joins = super().get_select_fields_and_joins(doctype)

		joins.append("left join `tabProject` proj on proj.name = o.project")
		select_fields += [
			"i.claim_customer",
			"proj.project_type", "proj.project_date",
			"proj.warranty_claim_denied", "proj.warranty_claim_denied_reason"
		]

		if self.filters.claim_billing_type:
			joins.append("left join `tabProject Type` ptype on ptype.name = proj.project_type")

		return select_fields, joins

	def get_date_field(self, doctype):
		if self.filters.date_type == "Project Date":
			return "proj.project_date"
		else:
			return super().get_date_field(doctype)

	def get_conditions(self, doctype):
		conditions = super().get_conditions(doctype)

		if self.filters.claim_customer:
			conditions.append("i.claim_customer = %(claim_customer)s")
		else:
			conditions.append("(i.claim_customer != '' and i.claim_customer is not null)")

		if self.filters.claim_billing_type:
			conditions.append("ptype.claim_billing_type = %(claim_billing_type)s")

		if self.filters.exclude_warranty_claim_denied:
			conditions.append("proj.warranty_claim_denied = 0")

		if self.filters.project_type:
			conditions.append("proj.project_type = %(project_type)s")

		return conditions

	def get_columns(self):
		columns = super().get_columns()

		if self.filters.date_type == "Project Date":
			transaction_date = next((c for c in columns if c.get("fieldname") == "transaction_date"), None)
			if transaction_date:
				transaction_date["fieldname"] = "project_date"
				transaction_date["label"] = _("Project Date")

		index = next((i for i, c in enumerate(columns) if c.get("fieldname") == "doctype"), 0)
		columns[index:index] = [
			{
				"label": _("Project Type"),
				"fieldname": "project_type",
				"fieldtype": "Link",
				"options": "Project Type",
				"width": 120
			},
		]

		index = next((i for i, c in enumerate(columns) if c.get("fieldname") == "project"), None)
		if index is not None:
			columns[index + 1:index + 1] = [
				{
					"label": _("Denied"),
					"fieldname": "warranty_claim_denied",
					"fieldtype": "Check",
					"width": 55
				},
			]

		return columns


def execute(filters=None):
	ReportClass = ClaimItemsToBeBilled

	hooks = frappe.get_hooks("override_claim_items_to_be_billed")
	if hooks:
		ReportClass = frappe.get_attr(hooks[-1])

	return ReportClass(filters).run("Customer")


@frappe.whitelist()
def make_claim_sales_invoice(data, customer):
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as invoice_from_sales_order
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as invoice_from_delivery_note

	if isinstance(data, str):
		data = json.loads(data)

	sales_orders = [d.get('name') for d in data if d.get('doctype') == "Sales Order" and d.get('claim_customer') == customer]
	sales_order_rows = [d.get('row_name') for d in data if d.get('doctype') == "Sales Order" and d.get('claim_customer') == customer]

	delivery_notes = [d.get('name') for d in data if d.get('doctype') == "Delivery Note" and d.get('claim_customer') == customer]
	delivery_note_rows = [d.get('row_name') for d in data if d.get('doctype') == "Delivery Note" and d.get('claim_customer') == customer]

	if not sales_orders and not delivery_notes:
		frappe.throw(_("No unbilled Sales Orders or Delivery Notes in report against Claim {0}")
			.format(frappe.get_desk_link("Customer", customer)))

	target_doc = frappe.new_doc("Sales Invoice")
	target_doc.customer = customer
	target_doc.bill_to = customer
	target_doc.claim_billing = 1
	target_doc.bill_multiple_projects = 1

	frappe.flags.selected_children = {}

	frappe.flags.selected_children['items'] = delivery_note_rows
	for name in delivery_notes:
		target_doc = invoice_from_delivery_note(name, target_doc, only_items=True, skip_postprocess=True)

	frappe.flags.selected_children['items'] = sales_order_rows
	for name in sales_orders:
		target_doc = invoice_from_sales_order(name, target_doc, only_items=True, skip_postprocess=True)

	target_doc.ignore_pricing_rule = 1
	target_doc.run_method("postprocess_after_mapping", reset_taxes=True)

	return target_doc
