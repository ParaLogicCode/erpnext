import frappe
from frappe import _
from crm.crm.doctype.lead.lead import Lead
from frappe.model.mapper import get_mapped_doc


class LeadERP(Lead):
	def on_trash(self):
		frappe.db.sql("update `tabIssue` set `lead` = '' where `lead` = %s", self.name)

	def update_customer_reference(self, customer, update_modified=True):
		self.db_set('customer', customer)

		status = None
		if customer:
			status = "Converted"
		elif self.status == "Converted":
			status = "Interested"

		if status:
			self.set_status(status=status, update=True, update_modified=update_modified)

	def is_opportunity(self):
		if super().is_opportunity():
			return True
		else:
			return self.has_quotation()

	def has_quotation(self):
		quotation = frappe.db.get_value("Quotation", {
			"quotation_to": "Lead",
			"party_name": self.name,
			"docstatus": 1,
			"status": ["!=", "Lost"]
		})

		return quotation

	def is_lost_opportunity(self):
		if super().is_lost_opportunity():
			return True
		else:
			return self.has_lost_quotation()

	def has_lost_quotation(self):
		quotation = frappe.db.get_value("Quotation", {
			"quotation_to": "Lead",
			"party_name": self.name,
			"docstatus": 1,
			"status": "Lost"
		})

		return quotation

	def is_converted(self):
		if self.get("customer"):
			return True
		else:
			return super().is_converted()


def get_customer_from_lead(lead, throw=False):
	if not lead:
		return None

	customer = frappe.db.get_value("Lead", lead, "customer")
	if not customer and throw:
		frappe.throw(_("Please convert Lead to Customer first"))

	return customer


@frappe.whitelist()
def make_customer(source_name, target_doc=None):
	return _make_customer(source_name, target_doc)


def _make_customer(source_name, target_doc=None, ignore_permissions=False):
	def set_missing_values(source, target):
		if source.company_name:
			target.customer_type = "Company"
			target.customer_name = source.company_name
		else:
			target.customer_type = "Individual"
			target.customer_name = source.lead_name

		target.customer_group = frappe.db.get_default("Customer Group")

	doclist = get_mapped_doc("Lead", source_name, {
		"Lead": {
			"doctype": "Customer",
			"field_map": {
				"name": "lead_name",
				"lead_name": "contact_first_name",
			}
		}
	}, target_doc, set_missing_values, ignore_permissions=ignore_permissions)

	return doclist


@frappe.whitelist()
def set_customer_for_lead(lead, customer):
	lead_doc = frappe.get_doc("Lead", lead)

	lead_doc.update_customer_reference(customer)
	lead_doc.notify_update()

	if customer:
		frappe.msgprint(_("{0} converted to {1}")
			.format(frappe.get_desk_link("Lead", lead), frappe.get_desk_link("Customer", customer)),
			indicator="green")
	else:
		frappe.msgprint(_("{0} unlinked with Customer").format(frappe.get_desk_link("Lead", lead)))


@frappe.whitelist()
def make_quotation(source_name, target_doc=None):
	def set_missing_values(source, target):
		add_sales_person_from_source(source, target)
		target.run_method("set_missing_values")
		target.run_method("reset_taxes_and_charges")
		target.run_method("calculate_taxes_and_totals")

	target_doc = get_mapped_doc("Lead", source_name, {
		"Lead": {
			"doctype": "Quotation",
			"field_map": {
				"name": "party_name",
				"doctype": "quotation_to",
			}
		}
	}, target_doc, set_missing_values)

	return target_doc


def add_sales_person_from_source(source, target):
	if target.meta.has_field('sales_team') and source.get('sales_person') and not target.get('sales_team'):
		target.append('sales_team', {
			'sales_person': source.sales_person,
			'allocated_percentage': 100,
		})


def override_lead_dashboard(data):
	data.setdefault("non_standard_fieldnames", {}).update({
		'Quotation': 'party_name',
	})

	data["transactions"].append({
		"label": _("Quotation"),
		"items": ["Quotation"]
	})

	return data
