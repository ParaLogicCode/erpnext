# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
from erpnext.projects.doctype.project.project import get_project_details
from erpnext.setup.doctype.terms_and_conditions.terms_and_conditions import get_terms_and_conditions
from erpnext.controllers.accounts_controller import AccountsController
from dateutil import relativedelta


class ServiceWarranty(AccountsController):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.status_map = [
			["Draft", None],
			["Active", "eval:self.docstatus == 1"],
			["Expired", "eval:self.docstatus == 1 and getdate(self.to_date) < getdate()"],
			["Cancelled", "eval:self.docstatus == 2"],
		]

	def validate(self):
		self.set_missing_values()
		self.validate_service_template()
		self.validate_project()
		self.validte_duplicate()
		self.set_date()
		self.set_terms()
		self.set_status()

	def on_submit(self):
		self.update_project()
		self.make_gl_entries()

	def on_cancel(self):
		self.update_project()
		self.make_gl_entries(cancel=True)

	def set_missing_values(self, for_validate=False):
		self.set_project_details()
		self.set_service_template_details()
		self.set_default_accounts()

	def set_project_details(self):
		if not self.project:
			return

		details = get_project_details(self.project, self.doctype)
		for field, value in details.items():
			if self.meta.has_field(field):
				self.set(field, value)

	def set_service_template_details(self):
		if not self.service_template:
			return

		template_doc = frappe.get_cached_doc("Service Template", self.service_template)

		self.service_template_name = template_doc.service_template_name

		if not self.warranty_validity:
			self.warranty_validity = cint(template_doc.warranty_validity)
		if not self.warranty_provision_amount:
			self.warranty_provision_amount = flt(template_doc.warranty_provision_amount)

		if template_doc.warranty_terms:
			self.tc_name = template_doc.warranty_terms

	def set_default_accounts(self):
		company = frappe.get_cached_doc("Company", self.company)
		if not self.warranty_provision_account:
			self.warranty_provision_account = company.service_warranty_provision_account
		if not self.warranty_expense_account:
			self.warranty_expense_account = company.service_warranty_expense_account

	def validate_service_template(self):
		if not self.project or not self.service_template:
			return

		includes_service_warranty = frappe.get_cached_value("Service Template", self.service_template, "includes_service_warranty")
		if not includes_service_warranty:
			frappe.throw(_("{0} does not include Service Warranty").format(
				frappe.get_desk_link("Service Template", self.service_template)
			))

	def validate_project(self):
		project = frappe.get_doc("Project", self.project)
		template_rows = [d for d in project.service_templates if d.service_template == self.service_template]
		if not template_rows:
			frappe.throw(_("{0} does not include Service Template {1}").format(
				frappe.get_desk_link("Project", self.project), frappe.bold(self.service_template)
			))

		if len(template_rows) == 1:
			self.service_template_detail = template_rows[0].name
			self.service_template_name = template_rows[0].service_template_name

		if self.from_date and getdate(self.from_date) < getdate(project.project_date):
			frappe.throw(_("From Date cannot be before {0} {1}").format(
				frappe.get_meta("Project").get_label("project_date"), frappe.bold(project.get_formatted("project_date"))
			))

	def validte_duplicate(self):
		if not self.project or not self.service_template:
			return

		filters = {
			"project": self.project,
			"service_template": self.service_template,
			"docstatus": 1,
		}
		if self.service_template_detail:
			filters["service_template_detail"] = self.service_template_detail
		if not self.is_new():
			filters["name"] = ["!=", self.name]

		duplicate = frappe.db.get_value("Service Warranty", filters=filters)
		if duplicate:
			frappe.throw(_("Service Warranty for {0} Service Template {1} already created: {2}").format(
				frappe.get_desk_link("Project", self.project),
				frappe.bold(self.service_template),
				frappe.get_desk_link("Service Warranty", duplicate)
			))

	def set_date(self):
		if not cint(self.warranty_validity):
			frappe.throw(_("Please set Warranty Validity"))

		from_date = getdate(self.from_date)
		if from_date > getdate():
			frappe.throw(_("From Date cannot be in the future"))

		self.to_date = from_date + relativedelta.relativedelta(months=cint(self.warranty_validity), days=-1)

	def set_terms(self):
		if self.get('tc_name'):
			doc = self.as_dict()
			self.terms = get_terms_and_conditions(self.tc_name, doc)
		else:
			self.terms = None

	def make_gl_entries(self, cancel=False):
		from erpnext.accounts.general_ledger import make_gl_entries

		gl_map = self.get_gl_entries()
		if gl_map:
			make_gl_entries(gl_map, cancel=cancel)

	def get_gl_entries(self):
		gl_map = []

		provision_amount = flt(self.warranty_provision_amount, self.precision("warranty_provision_amount"))
		if not provision_amount:
			return gl_map

		if not self.warranty_provision_account:
			frappe.throw(_("Please set Warranty Provision Account"))
		if not self.warranty_expense_account:
			frappe.throw(_("Please set Warranty Expense Account"))

		gl_map.append(self.get_gl_dict({
			"account": self.warranty_expense_account,
			"against": self.warranty_provision_account,
			"debit": provision_amount,
			"debit_in_account_currency": provision_amount,
			"remarks": _("Warranty Provision Entry"),
			"cost_center": self.get('cost_center'),
			"project": self.get('project'),
		}))

		gl_map.append(self.get_gl_dict({
			"account": self.warranty_provision_account,
			"against": self.warranty_expense_account,
			"credit": provision_amount,
			"credit_in_account_currency": provision_amount,
			"remarks": _("Warranty Provision Entry"),
			"cost_center": self.get('cost_center'),
			"project": self.get('project'),
		}))

		return gl_map

	def update_project(self):
		if self.project:
			doc = frappe.get_doc("Project", self.project)

			doc.validate_project_status_for_transaction(self)
			if self.docstatus == 1:
				doc.validate_for_transaction(self)

			doc.set_service_template_has_transaction(update=True)
			doc.set_status(update=True)
			doc.notify_update()


def set_warranty_service_expired():
	frappe.db.sql("""
		update `tabService Warranty`
		set status = 'Expired'
		where status = 'Active' and docstatus = 1 and to_date < %s
	""", getdate())
