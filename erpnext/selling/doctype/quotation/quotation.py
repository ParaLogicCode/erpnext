# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt, nowdate, getdate, cint, add_days, date_diff
from frappe import _
from erpnext.overrides.lead.lead_hooks import get_customer_from_lead

from erpnext.controllers.selling_controller import SellingController

form_grid_templates = {
	"items": "templates/form_grid/item_grid.html"
}


class Quotation(SellingController):
	def __init__(self, *args, **kwargs):
		super(Quotation, self).__init__(*args, **kwargs)
		self.status_map = [
			["Draft", None],
			["Open", "eval:self.docstatus==1"],
			["Expired", "eval:self.valid_till and getdate(self.valid_till) < getdate()"],
			["Lost", "eval:self.status=='Lost'"],
			["Ordered", "has_sales_order_or_invoice"],
			["Cancelled", "eval:self.docstatus==2"],
		]

	def validate(self):
		super(Quotation, self).validate()
		self.validate_uom_is_integer("stock_uom", "qty")
		self.validate_quotation_valid_till()
		self.validate_delivery_date()
		self.validate_with_previous_doc()
		self.clear_approval_date()
		self.set_customer_name()

		if self.items:
			self.with_items = 1

		self.set_ordered_status()
		self.set_status()
		self.set_title()

	def before_submit(self):
		self.validate_item_code_mandatory()

	def on_submit(self):
		# Check for Approving Authority
		frappe.get_doc('Authorization Control').validate_approving_authority(self.doctype,
			self.company, self.base_grand_total, self)

		# update enquiry status
		self.update_opportunity()
		self.update_lead_status()
		self.trigger_project_status()

	def on_cancel(self):
		if self.lost_reasons:
			self.lost_reasons = []

		# update enquiry status
		self.update_status_on_cancel()
		self.update_opportunity()
		self.update_lead_status(status="Interested")
		self.trigger_project_status()

	def clear_approval_date(self):
		self.approval_date = None

	def before_update_after_submit(self):
		super().before_update_after_submit()
		self.validate_approval_date()

	def onload(self):
		super(Quotation, self).onload()
		if self.quotation_to == "Customer":
			self.set_onload('customer', self.party_name)
		elif self.quotation_to == "Lead":
			self.set_onload('customer', get_customer_from_lead(self.party_name))

	def set_title(self):
		if self.get('bill_to') and self.bill_to != self.customer:
			self.title = "{0} ({1})".format(self.bill_to_name or self.bill_to, self.customer_name or self.customer)
		else:
			self.title = self.customer_name or self.customer

	def set_missing_values(self, for_validate=False):
		super().set_missing_values(for_validate)
		self.set_missing_delivery_date()

	def validate_delivery_date(self):
		if cint(self.lead_time_days) < 0:
			frappe.throw(_("{0} cannot be negative").format(self.meta.get_label("lead_time_days")))

		if self.delivery_date and getdate(self.delivery_date) < getdate(self.transaction_date):
			frappe.throw(_("Expected Delivery Date must be after Quotation Date"))

	def validate_approval_date(self):
		if self.approval_date and getdate(self.approval_date) < getdate(self.transaction_date):
			frappe.throw(_("Approval Date must be after Quotation Date"))
		if self.approval_date and getdate(self.approval_date) > getdate():
			frappe.throw(_("Approval Date cannot be in the future"))

	def set_missing_delivery_date(self):
		if cint(self.lead_time_days) > 0:
			self.delivery_date = add_days(getdate(self.transaction_date), cint(self.lead_time_days))

		if not cint(self.lead_time_days) and self.delivery_date:
			self.lead_time_days = date_diff(self.delivery_date, self.transaction_date)
			if self.lead_time_days < 0:
				self.lead_time_days = 0

	def validate_with_previous_doc(self):
		super().validate_with_previous_doc({
			"Sales Order Item": {
				"ref_dn_field": "sales_order_item",
				"compare_fields": [["item_code", "="], ["uom", "="], ["conversion_factor", "="], ["qty", "="], ["rate", "="]],
				"is_child_table": True,
				"allow_duplicate_prev_row_id": True
			},
		})

	def set_ordered_status(self, update=False, update_modified=True):
		ordered_qty_map = self.get_ordered_qty_map()
		for d in self.items:
			d.ordered_qty = flt(ordered_qty_map.get(d.name))
			if update:
				d.db_set({
					'ordered_qty': d.ordered_qty,
				}, update_modified=update_modified)

	def get_ordered_qty_map(self):
		ordered_qty_map = {}

		if self.docstatus == 1:
			row_names = [d.name for d in self.items]

			if row_names:
				ordered_qty_map = dict(frappe.db.sql("""
					select i.quotation_item, sum(i.qty)
					from `tabSales Order Item` i
					inner join `tabSales Order` p on p.name = i.parent
					where p.docstatus = 1 and i.quotation_item in %s
					group by i.quotation_item
				""", [row_names]))

			unordered_rows = list(set(row_names) - set(ordered_qty_map.keys()))
			if unordered_rows:
				billed_qty_map = dict(frappe.db.sql("""
					select i.quotation_item, sum(i.qty)
					from `tabSales Invoice Item` i
					inner join `tabSales Invoice` p on p.name = i.parent
					where p.docstatus = 1 and i.quotation_item in %s
					group by i.quotation_item
				""", [row_names]))

				for quotation_item, billed_qty in billed_qty_map.items():
					if quotation_item not in ordered_qty_map:
						ordered_qty_map[quotation_item] = billed_qty

		return ordered_qty_map

	def update_lead_status(self, status=None):
		if self.quotation_to == "Lead" and self.party_name:
			doc = frappe.get_doc("Lead", self.party_name)
			doc.set_status(update=True, status=status)
			doc.notify_update()

	def update_opportunity(self):
		if self.get('opportunity'):
			opp = frappe.get_doc("Opportunity", self.opportunity)
			opp.set_status(update=True)
			opp.notify_update()

	def has_sales_order_or_invoice(self):
		return frappe.db.get_value("Sales Order Item", {"quotation": self.name, "docstatus": 1})\
			or frappe.db.get_value("Sales Invoice Item", {"quotation": self.name, "docstatus": 1})

	@frappe.whitelist()
	def set_is_lost(self, is_lost, lost_reasons_list=None, detailed_reason=None):
		is_lost = cint(is_lost)

		if is_lost and self.has_sales_order_or_invoice():
			frappe.throw(_("Cannot declare as Lost because Quotation is converted to order"))

		if is_lost:
			self.set_status(update=True, status="Lost")
			self.db_set('order_lost_reason', detailed_reason)
			self.lost_reasons = []
			for reason in lost_reasons_list or []:
				self.append('lost_reasons', reason)
		else:
			self.set_status(update=True, status="Open")
			self.db_set('order_lost_reason', None)
			self.lost_reasons = []

		self.update_child_table("lost_reasons")

		if self.get('opportunity') and not self.flags.from_opportunity:
			opp = frappe.get_doc("Opportunity", self.opportunity)
			opp.set_is_lost(is_lost, lost_reasons_list, detailed_reason)

		self.update_lead_status()
		self.notify_update()

	def set_customer_name(self):
		if self.party_name and self.quotation_to == 'Customer':
			self.customer_name = frappe.get_cached_value("Customer", self.party_name, "customer_name")
		elif self.party_name and self.quotation_to == 'Lead':
			lead_name, company_name = frappe.db.get_value("Lead", self.party_name, ["lead_name", "company_name"])
			self.customer_name = company_name or lead_name

	def print_other_charges(self,docname):
		print_lst = []
		for d in self.get('taxes'):
			lst1 = []
			lst1.append(d.description)
			lst1.append(d.total)
			print_lst.append(lst1)
		return print_lst

	def on_recurring(self, reference_doc, auto_repeat_doc):
		self.valid_till = None


@frappe.whitelist()
def make_sales_order(source_name, target_doc=None):
	quotation = frappe.db.get_value("Quotation", source_name, ["transaction_date", "valid_till"], as_dict = 1)
	if quotation.valid_till and (quotation.valid_till < quotation.transaction_date or quotation.valid_till < getdate(nowdate())):
		frappe.throw(_("Validity period of this quotation has ended."))
	return _make_sales_order(source_name, target_doc=target_doc)


def _make_sales_order(
	source_name,
	target_doc=None,
	ignore_permissions=False,
	skip_item_mapping=False,
	skip_postprocess=False,
):
	def postprocess(source, target):
		customer = get_customer_from_quotation(source)
		if customer:
			target.customer = customer.name
			target.customer_name = customer.customer_name

		if source.referral_sales_partner:
			target.sales_partner = source.referral_sales_partner
			target.commission_rate = frappe.get_value('Sales Partner', source.referral_sales_partner, 'commission_rate')

		target.ignore_pricing_rule = 1
		target.flags.ignore_permissions = ignore_permissions
		target.run_method("postprocess_after_mapping")

	mapping = {
		"Quotation": {
			"doctype": "Sales Order",
			"validation": {
				"docstatus": ["=", 1]
			},
			"field_map": {
				"remarks": "remarks"
			}
		},
		"Sales Taxes and Charges": {
			"doctype": "Sales Taxes and Charges",
			"add_if_empty": True
		},
		"Sales Team": {
			"doctype": "Sales Team",
			"add_if_empty": True
		},
		"Payment Schedule": {
			"doctype": "Payment Schedule",
			"add_if_empty": True
		}
	}

	if not skip_item_mapping:
		mapping["Quotation Item"] = get_item_mapper_for_sales_order()

	return get_mapped_doc("Quotation", source_name, mapping, target_doc,
		postprocess=postprocess if not skip_postprocess else None,
		ignore_permissions=ignore_permissions)


def get_item_mapper_for_sales_order():
	def item_condition(source, source_parent, target_parent):
		if source.name in [d.quotation_item for d in target_parent.get('items') if d.quotation_item]:
			return False

		if source.sales_order:
			return False

		return True

	def update_item(obj, target, source_parent, target_parent):
		pass

	return {
		"doctype": "Sales Order Item",
		"field_map": {
			"parent": "quotation",
			"name": "quotation_item",
			"service_template": "service_template",
			"service_template_detail": "service_template_detail",
		},
		"condition": item_condition,
		"postprocess": update_item,
	}


def set_expired_status():
	frappe.db.sql("""
		UPDATE
			`tabQuotation` SET `status` = 'Expired'
		WHERE
			`status` not in ('Ordered', 'Expired', 'Lost', 'Cancelled') AND `valid_till` < %s AND `docstatus` = 1
		""", (nowdate()))


@frappe.whitelist()
def make_sales_invoice(source_name, target_doc=None):
	return _make_sales_invoice(source_name, target_doc)


def _make_sales_invoice(source_name, target_doc=None, ignore_permissions=False):
	def set_missing_values(source, target):
		customer = get_customer_from_quotation(source)
		if customer:
			target.customer = customer.name
			target.customer_name = customer.customer_name

		target.ignore_pricing_rule = 1
		target.flags.ignore_permissions = ignore_permissions
		target.run_method("postprocess_after_mapping")

	def update_item(source, target, source_parent, target_parent):
		target.project = source_parent.get('project')
		target.cost_center = None
		target.depreciation_percentage = None

		if target_parent:
			target_parent.adjust_rate_for_claim_item(source, target)

	doclist = get_mapped_doc("Quotation", source_name, {
			"Quotation": {
				"doctype": "Sales Invoice",
				"validation": {
					"docstatus": ["=", 1]
				},
				"field_no_map": [
					"has_stin",
				],
				"field_map": {
					"remarks": "remarks"
				}
			},
			"Quotation Item": {
				"doctype": "Sales Invoice Item",
				"field_map": {
					"parent": "quotation",
					"name": "quotation_item",
				},
				"postprocess": update_item
			},
			"Sales Taxes and Charges": {
				"doctype": "Sales Taxes and Charges",
				"add_if_empty": True
			},
			"Sales Team": {
				"doctype": "Sales Team",
				"add_if_empty": True
			}
		}, target_doc, set_missing_values, ignore_permissions=ignore_permissions)

	return doclist


def get_customer_from_quotation(quotation):
	if quotation and quotation.get('party_name'):
		if quotation.get('quotation_to') == 'Lead':
			customer = get_customer_from_lead(quotation.get("party_name"), throw=True)
			return frappe.get_cached_doc("Customer", customer)

		elif quotation.get('quotation_to') == 'Customer':
			return frappe.get_cached_doc("Customer", quotation.get("party_name"))
