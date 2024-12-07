import frappe
from frappe import _
from crm.crm.doctype.opportunity.opportunity import Opportunity
from frappe.model.mapper import get_mapped_doc
from erpnext.utilities.transaction_base import validate_uom_is_integer
from erpnext.stock.get_item_details import get_applies_to_details, get_force_applies_to_fields
from erpnext.setup.utils import get_exchange_rate
from erpnext.accounts.party import get_party_account_currency
from erpnext.overrides.lead.lead_hooks import get_customer_from_lead


class OpportunityERP(Opportunity):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		self.force_item_fields = ["item_group", "brand"]
		self.force_applies_to_fields = get_force_applies_to_fields(self.doctype)

	def onload(self):
		super().onload()

		if self.opportunity_from == "Customer":
			self.set_onload('customer', self.party_name)
		elif self.opportunity_from == "Lead":
			self.set_onload('customer', get_customer_from_lead(self.party_name))

	def validate(self):
		super().validate()
		validate_uom_is_integer(self, "uom", "qty")
		self.validate_financer()
		self.validate_maintenance_schedule()

	@classmethod
	def get_allowed_party_types(cls):
		return super().get_allowed_party_types() + ["Customer"]

	def set_missing_values(self):
		super().set_missing_values()
		self.set_item_details()
		self.set_applies_to_details()

	def validate_financer(self):
		if self.get('financer'):
			if self.get('opportunity_from') == "Customer" and self.get('party_name') == self.get('financer'):
				frappe.throw(_("Customer and Financer cannot be the same"))

		elif self.meta.has_field('financer'):
			self.financer_name = None
			self.finance_type = None

	def validate_maintenance_schedule(self):
		if not self.get("maintenance_schedule"):
			return

		filters = {
			'maintenance_schedule': self.maintenance_schedule,
			'maintenance_schedule_row': self.maintenance_schedule_row
		}
		if not self.is_new():
			filters['name'] = ['!=', self.name]

		dup = frappe.get_value("Opportunity", filters=filters)
		if dup:
			frappe.throw(_("{0} already exists for this scheduled maintenance".format(frappe.get_desk_link("Opportunity", dup))))

	def set_item_details(self):
		for d in self.items:
			if not d.item_code:
				continue

			item_details = get_item_details(d.item_code)
			for k, v in item_details.items():
				if d.meta.has_field(k) and (not d.get(k) or k in self.force_item_fields):
					d.set(k, v)

	def set_applies_to_details(self):
		args = self.as_dict()
		applies_to_details = get_applies_to_details(args, for_validate=True)

		for k, v in applies_to_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in self.force_applies_to_fields:
				self.set(k, v)

	def is_converted(self):
		if self.is_new():
			return super().is_converted()

		if self.has_ordered_quotation():
			return True

		return super().is_converted()

	def has_active_quotation(self):
		quotations = get_active_quotations(self.name)
		if quotations:
			return True

		return super().has_active_quotation()

	def has_lost_quotation(self):
		lost_quotations = self.get_lost_quotations()
		if lost_quotations:
			return True

		return super().has_lost_quotation()

	def has_ordered_quotation(self):
		if self.is_new():
			return None

		quotation = frappe.db.get_value("Quotation", {
			"opportunity": self.name,
			"docstatus": 1,
			"status": "Ordered",
		})

		return quotation

	def get_lost_quotations(self):
		if self.is_new():
			return []

		lost_quotations = frappe.get_all("Quotation", {
			"opportunity": self.name,
			"docstatus": 1,
			"status": 'Lost'
		})

		return [d.name for d in lost_quotations]

	def set_next_document_is_lost(self, is_lost, lost_reasons_list=None, detailed_reason=None):
		super().set_next_document_is_lost(is_lost, lost_reasons_list, detailed_reason)

		quotations = get_active_quotations(self.name) if is_lost else self.get_lost_quotations()
		for name in quotations:
			doc = frappe.get_doc("Quotation", name)
			doc.flags.from_opportunity = True
			doc.set_is_lost(is_lost, lost_reasons_list, detailed_reason)


def get_active_quotations(opportunity):
	quotations = frappe.get_all('Quotation', {
		'opportunity': opportunity,
		'status': ("not in", ['Lost', 'Closed']),
		'docstatus': 1
	}, 'name')

	return [d.name for d in quotations]


@frappe.whitelist()
def get_item_details(item_code):
	item_details = frappe.get_cached_doc("Item", item_code) if item_code else frappe._dict()

	return {
		'item_name': item_details.item_name,
		'description': item_details.description,
		'uom': item_details.stock_uom,
		'image': item_details.image,
		'item_group': item_details.item_group,
		'brand': item_details.brand,
	}


@frappe.whitelist()
def make_quotation(source_name, target_doc=None):
	from erpnext.overrides.lead.lead_hooks import add_sales_person_from_source

	def set_missing_values(source, target):
		company_currency = frappe.get_cached_value('Company',  target.company,  "default_currency")

		if target.quotation_to == 'Customer' and target.party_name:
			party_account_currency = get_party_account_currency("Customer", target.party_name, target.company)
		else:
			party_account_currency = company_currency

		target.currency = party_account_currency or company_currency

		if company_currency == target.currency:
			exchange_rate = 1
		else:
			exchange_rate = get_exchange_rate(target.currency, company_currency,
				target.transaction_date, args="for_selling")

		target.conversion_rate = exchange_rate

		add_sales_person_from_source(source, target)
		target.run_method("postprocess_after_mapping")

	doclist = get_mapped_doc("Opportunity", source_name, {
		"Opportunity": {
			"doctype": "Quotation",
			"field_map": {
				"opportunity_from": "quotation_to",
				"opportunity_type": "order_type",
				"name": "opportunity",
				"applies_to_serial_no": "applies_to_serial_no",
			}
		},
		"Opportunity Item": {
			"doctype": "Quotation Item",
			"field_map": {
				"uom": "stock_uom",
			},
			"add_if_empty": True
		}
	}, target_doc, set_missing_values)

	return doclist


@frappe.whitelist()
def make_request_for_quotation(source_name, target_doc=None):
	doclist = get_mapped_doc("Opportunity", source_name, {
		"Opportunity": {
			"doctype": "Request for Quotation"
		},
		"Opportunity Item": {
			"doctype": "Request for Quotation Item",
			"field_map": [
				["name", "opportunity_item"],
				["parent", "opportunity"],
				["uom", "uom"]
			]
		}
	}, target_doc)

	return doclist


def get_customer_from_opportunity(source):
	if source and source.get('party_name'):
		if source.get('opportunity_from') == 'Lead':
			customer = get_customer_from_lead(source.get('party_name'), throw=True)
			return frappe.get_cached_doc('Customer', customer)

		elif source.get('opportunity_from') == 'Customer':
			return frappe.get_cached_doc('Customer', source.get('party_name'))


@frappe.whitelist()
def make_supplier_quotation(source_name, target_doc=None):
	doclist = get_mapped_doc("Opportunity", source_name, {
		"Opportunity": {
			"doctype": "Supplier Quotation",
			"field_map": {
				"name": "opportunity"
			}
		},
		"Opportunity Item": {
			"doctype": "Supplier Quotation Item",
			"field_map": {
				"uom": "stock_uom"
			}
		}
	}, target_doc)

	return doclist


def override_opportunity_dashboard(data):
	data["transactions"].insert(0, {
		"label": _("Quotation"),
		"items": ["Quotation", "Supplier Quotation"]
	})

	return data
