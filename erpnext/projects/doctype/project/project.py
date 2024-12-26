# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
import erpnext
from frappe import _
from frappe.utils import flt, cint, cstr, today, add_days, ceil, getdate, clean_whitespace
from erpnext.stock.get_item_details import get_applies_to_details, get_force_applies_to_fields
from frappe.model.naming import set_name_by_naming_series
from frappe.model.utils import get_fetch_values
from frappe.contacts.doctype.address.address import get_default_address
from frappe.contacts.doctype.contact.contact import get_default_contact, get_all_contact_nos
from erpnext.accounts.party import get_contact_details, get_address_display
from erpnext.controllers.status_updater import StatusUpdaterERP
from erpnext.projects.doctype.project_status.project_status import get_auto_project_status, set_manual_project_status,\
	get_valid_manual_project_status_names, is_manual_project_status, validate_project_status_for_transaction
from frappe.model.meta import get_field_precision
import json


class Project(StatusUpdaterERP):
	def __init__(self, *args, **kwargs):
		super(Project, self).__init__(*args, **kwargs)

		self.force_customer_fields = [
			"customer_name", "customer_group",
			"bill_to_name", "bill_to_customer_group",
			"tax_id", "tax_cnic", "tax_strn", "tax_status",
			"address_display", "contact_display", "contact_email",
			"secondary_contact_display",
		]

		self.force_applies_to_fields = get_force_applies_to_fields(self.doctype)

		self.sales_data = frappe._dict()
		self.consumables_data = frappe._dict()
		self.invoices = []

	def get_feed(self):
		return '{0}: {1}'.format(_(self.status), frappe.safe_decode(self.project_name or self.name))

	def autoname(self):
		project_naming_by = frappe.defaults.get_global_default('project_naming_by')
		if project_naming_by == 'Project Name':
			self.name = self.project_name
		else:
			set_name_by_naming_series(self, 'project_number')

	def onload(self):
		self.set_onload('activity_summary', self.get_activity_summary())
		self.set_onload('cant_change_fields', self.get_cant_change_fields())
		self.set_onload('valid_manual_project_status_names', get_valid_manual_project_status_names(self))
		self.set_onload('is_manual_project_status', is_manual_project_status(self.project_status))
		self.set_onload('contact_nos', get_all_contact_nos('Customer', self.customer))
		self.set_onload('task_count', self.get_task_count())

		self.sales_data = self.get_project_sales_data(get_sales_invoice=True)
		self.consumables_data = self.get_project_consumables_data()
		self.set_items_and_totals_html_onload(self.sales_data, self.consumables_data)

	def before_print(self, print_settings=None):
		self.company_address_doc = erpnext.get_company_address_doc(self)
		self.sales_data = self.get_project_sales_data(get_sales_invoice=True)
		self.consumables_data = self.get_project_consumables_data()
		self.get_sales_invoice_names()

	def before_validate(self):
		pass

	def validate(self):
		if self.status not in ['Completed', 'Closed']:
			self.set_missing_values()

		self.validate_appointment()
		self.validate_phone_nos()
		self.validate_project_type()
		self.validate_cash_billing()
		self.validate_readings()
		self.validate_depreciation()
		self.validate_warranty()

		self.set_title()

		self.set_tasks_status()
		self.set_percent_complete()
		self.set_project_date()
		self.set_billing_and_delivery_status()
		self.set_costing()
		self.run_method("set_additional_status")
		self.set_status()

		self.validate_cant_change()

		self._previous_appointment = self.db_get('appointment')

	def on_update(self):
		self.update_appointment()
		self.handle_on_status_change()

	def handle_on_status_change(self):
		if self.flags.status_changed:
			self.run_method("on_status_change")
			self.flags.status_changed = False

	def before_insert(self):
		self.validate_appointment_required()

	def after_insert(self):
		self.set_project_in_sales_order_and_quotation()

	def after_delete(self):
		self.update_appointment()

	def on_status_change(self):
		pass

	def set_additional_status(self):
		pass

	def set_title(self):
		if self.project_name:
			self.title = self.project_name
		elif self.customer_name or self.customer:
			self.title = self.customer_name or self.customer
		else:
			self.title = self.name

	def set_billing_and_delivery_status(self, update=False, update_modified=False):
		sales_data = self.get_project_sales_data(get_sales_invoice=False)
		self.total_billable_amount = sales_data.totals.grand_total
		self.customer_billable_amount = sales_data.totals.customer_grand_total
		self.total_billed_amount = self.get_billed_amount()

		sales_orders = frappe.get_all("Sales Order", fields=['billing_status', 'delivery_status', 'status', 'skip_delivery_note'], filters={
			"project": self.name, "docstatus": 1
		})
		delivery_notes = frappe.get_all("Delivery Note", fields=['billing_status', 'status'], filters={
			"project": self.name, "docstatus": 1, "is_return": 0,
		})
		sales_invoices = self.get_sales_invoices()

		self.billing_status, self.to_bill = self.get_billing_status(sales_orders, delivery_notes, sales_invoices, self.total_billed_amount)
		self.delivery_status, self.to_deliver = self.get_delivery_status(sales_orders, delivery_notes)

		if update:
			self.db_set({
				'total_billable_amount': self.total_billable_amount,
				'customer_billable_amount': self.customer_billable_amount,
				'total_billed_amount': self.total_billed_amount,
				'billing_status': self.billing_status,
				'to_bill': self.to_bill,
				'delivery_status': self.delivery_status,
				'to_deliver': self.to_deliver,
			}, None, update_modified=update_modified)

	def get_billing_status(self, sales_orders, delivery_notes, sales_invoices, total_billed_amount):
		has_billables = False
		has_unbilled = False
		has_sales_invoice = False

		for d in sales_orders + delivery_notes:
			if d.status != "Closed":
				has_billables = True
				if d.billing_status == "To Bill":
					has_unbilled = True

		if sales_invoices:
			has_sales_invoice = True

		if has_billables:
			if has_sales_invoice:
				if has_unbilled:
					if flt(total_billed_amount) > 0:
						billing_status = "Partly Billed"
						to_bill = 1
					else:
						billing_status = "Not Billed"
						to_bill = 1
				else:
					billing_status = "Fully Billed"
					to_bill = 0
			else:
				billing_status = "Not Billed"
				to_bill = 1
		else:
			if has_sales_invoice:
				billing_status = "Fully Billed"
				to_bill = 0
			else:
				billing_status = "Not Applicable"
				to_bill = 0

		return billing_status, to_bill

	def get_delivery_status(self, sales_orders, delivery_notes):
		has_deliverables = False
		has_undelivered = False
		has_delivery_note = False

		if delivery_notes:
			has_delivery_note = True

		for d in sales_orders:
			if d.status != 'Closed' and not d.skip_delivery_note:
				has_deliverables = True
				if d.delivery_status == "To Deliver":
					has_undelivered = True

		if has_deliverables:
			if has_delivery_note:
				if has_undelivered:
					delivery_status = "Partly Delivered"
					to_deliver = 1
				else:
					delivery_status = "Fully Delivered"
					to_deliver = 0
			else:
				delivery_status = "Not Delivered"
				to_deliver = 1
		else:
			if has_delivery_note:
				delivery_status = "Fully Delivered"
				to_deliver = 0
			else:
				delivery_status = "Not Applicable"
				to_deliver = 0

		return delivery_status, to_deliver

	def get_billed_amount(self):
		directly_billed = frappe.db.sql("""
			select sum(base_grand_total)
			from `tabSales Invoice`
			where project = %s and docstatus = 1
		""", self.name)
		directly_billed = flt(directly_billed[0][0]) if directly_billed else 0

		indirectly_billed = frappe.db.sql("""
			select sum(i.base_tax_inclusive_amount)
			from `tabSales Invoice Item` i
			inner join `tabSales Invoice` p on p.name = i.parent
			where i.project = %(project)s and ifnull(p.project, '') != %(project)s and p.docstatus = 1
		""", {'project': self.name})
		indirectly_billed = flt(indirectly_billed[0][0]) if indirectly_billed else 0

		grand_total_precision = get_field_precision(frappe.get_meta("Sales Invoice").get_field("grand_total"),
			currency=frappe.get_cached_value('Company', self.company, "default_currency"))
		return flt(directly_billed + indirectly_billed, grand_total_precision)

	def set_costing(self, update=False, update_modified=False):
		self.set_sales_amount(update=update, update_modified=update_modified)
		self.set_timesheet_values(update=update, update_modified=update_modified)
		self.set_expense_claim_values(update=update, update_modified=update_modified)
		self.set_purchase_values(update=update, update_modified=update_modified)
		self.set_material_consumed_cost(update=update, update_modified=update_modified)
		self.set_gross_margin(update=update, update_modified=update_modified)

	def set_sales_amount(self, update=False, update_modified=False):
		sales_data = self.get_project_sales_data(get_sales_invoice=True)
		self.total_sales_amount = sales_data.totals.net_total
		self.material_sales_amount = sales_data.material_items.net_total
		self.part_sales_amount = sales_data.part_items.net_total
		self.lubricant_sales_amount = sales_data.lubricant_items.net_total
		self.service_sales_amount = sales_data.service_items.net_total
		self.labour_sales_amount = sales_data.labour_items.net_total
		self.sublet_sales_amount = sales_data.sublet_items.net_total

		if update:
			self.db_set({
				'total_sales_amount': self.total_sales_amount,
				'material_sales_amount': self.material_sales_amount,
				'part_sales_amount': self.part_sales_amount,
				'lubricant_sales_amount': self.lubricant_sales_amount,
				'service_sales_amount': self.service_sales_amount,
				'labour_sales_amount': self.labour_sales_amount,
				'sublet_sales_amount': self.sublet_sales_amount,
			}, None, update_modified=update_modified)

	def set_timesheet_values(self, update=False, update_modified=False):
		time_sheet_data = frappe.db.sql("""
			select
				sum(costing_amount) as costing_amount,
				sum(billing_amount) as billing_amount,
				min(from_time) as start_date,
				max(to_time) as end_date,
				sum(hours) as time
			from `tabTimesheet Detail`
			where project = %s and docstatus = 1
		""", self.name, as_dict=1)[0]

		self.actual_start_date = time_sheet_data.start_date
		self.actual_end_date = time_sheet_data.end_date

		self.timesheet_costing_amount = flt(time_sheet_data.costing_amount)
		self.timesheet_billable_amount = flt(time_sheet_data.billing_amount)
		self.actual_time = flt(time_sheet_data.time)

		if update:
			self.db_set({
				'actual_start_date': self.actual_start_date,
				'actual_end_date': self.actual_end_date,
				'timesheet_costing_amount': self.timesheet_costing_amount,
				'timesheet_billable_amount': self.timesheet_billable_amount,
				'actual_time': self.actual_time,
			}, None, update_modified=update_modified)

	def set_expense_claim_values(self, update=False, update_modified=False):
		expense_claim_data = frappe.db.sql("""
			select sum(sanctioned_amount) as total_sanctioned_amount
			from `tabExpense Claim Detail`
			where project = %s and docstatus = 1
		""", self.name, as_dict=1)[0]

		self.total_expense_claim = flt(expense_claim_data.total_sanctioned_amount)

		if update:
			self.db_set({
				'total_expense_claim': self.total_expense_claim,
			}, None, update_modified=update_modified)

	def set_purchase_values(self, update=False, update_modified=False):
		total_purchase_cost = frappe.db.sql("""
			select sum(base_net_amount)
			from `tabPurchase Invoice Item`
			where project = %s and docstatus=1
		""", self.name)

		self.total_purchase_cost = flt(total_purchase_cost[0][0]) if total_purchase_cost else 0

		if update:
			self.db_set({
				'total_purchase_cost': self.total_purchase_cost,
			}, None, update_modified=update_modified)

	def set_material_consumed_cost(self, update=False, update_modified=False):
		amount = frappe.db.sql("""
			select ifnull(sum(sed.amount), 0)
			from `tabStock Entry` se, `tabStock Entry Detail` sed
			where se.docstatus = 1 and se.project = %s and sed.parent = se.name
				and (sed.t_warehouse is null or sed.t_warehouse = '')
		""", self.name, as_list=1)
		amount = flt(amount[0][0]) if amount else 0

		additional_costs = frappe.db.sql("""
			select ifnull(sum(sed.amount), 0)
			from `tabStock Entry` se, `tabStock Entry Taxes and Charges` sed
			where se.docstatus = 1 and se.project = %s and sed.parent = se.name
				and se.purpose = 'Manufacture'""", self.name, as_list=1)
		additional_cost_amt = flt(additional_costs[0][0]) if additional_costs else 0

		amount += additional_cost_amt

		self.total_consumed_material_cost = amount

		if update:
			self.db_set({
				'total_consumed_material_cost': self.total_consumed_material_cost,
			}, None, update_modified=update_modified)

	def set_gross_margin(self, update=False, update_modified=False):
		total_revenue = flt(self.total_sales_amount)
		total_expense = (flt(self.timesheet_costing_amount) + flt(self.total_expense_claim)
			+ flt(self.total_purchase_cost) + flt(self.total_consumed_material_cost))

		self.gross_margin = total_revenue - total_expense
		self.per_gross_margin = self.gross_margin / total_revenue * 100 if total_revenue else 0

		if update:
			self.db_set({
				'gross_margin': self.gross_margin,
				'per_gross_margin': self.per_gross_margin,
			}, None, update_modified=update_modified)

	def set_tasks_status(self, update=False, update_modified=False):
		tasks_data = frappe.get_all(
			"Task",
			fields=["name", "status", "assigned_to", "task_type"],
			filters={
				"project": self.name,
				"status": ["!=", "Cancelled"],
			},
			order_by="creation asc",
		)

		self.current_task_type = None

		if not tasks_data:
			self.tasks_status = "No Tasks"
		elif all(d.status == "Completed" for d in tasks_data):
			self.tasks_status = "Completed"
		elif current_tasks := [d for d in tasks_data if d.status == "Working"]:
			self.tasks_status = "In Progress"
			self.current_task_type = current_tasks[0].task_type
		elif current_tasks := [d for d in tasks_data if d.status == "On Hold"]:
			self.tasks_status = "On Hold"
			self.current_task_type = current_tasks[0].task_type
		elif current_tasks := [d for d in tasks_data if d.status == "Open" and d.assigned_to]:
			self.tasks_status = "Assigned"
			self.current_task_type = current_tasks[0].task_type
		else:
			self.tasks_status = "To Assign"

		if update:
			self.db_set({
				"tasks_status": self.tasks_status,
				"current_task_type": self.current_task_type,
			}, update_modified=update_modified)

	def get_task_count(self):
		tasks_data = frappe.get_all("Task", pluck="status", filters={
			"project": self.name,
			"status": ["!=", "Cancelled"],
		})

		count = frappe._dict({
			"total_tasks": len(tasks_data),
			"completed_tasks": len([status for status in tasks_data if status == "Completed"]),
		})

		return count

	def set_percent_complete(self, update=False, update_modified=False):
		if self.percent_complete_method == "Manual":
			if self.status == "Completed":
				self.percent_complete = 100
			return

		total = frappe.db.count('Task', dict(project=self.name))

		if not total:
			self.percent_complete = 0
		else:
			if (self.percent_complete_method == "Task Completion" and total > 0) or (not self.percent_complete_method and total > 0):
				completed = frappe.db.sql("""
					select count(name)
					from tabTask where
					project=%s and status in ('Cancelled', 'Completed')
				""", self.name)[0][0]
				self.percent_complete = flt(flt(completed) / total * 100, 2)

			if self.percent_complete_method == "Task Progress" and total > 0:
				progress = frappe.db.sql("""select sum(progress) from tabTask where project=%s""", self.name)[0][0]
				self.percent_complete = flt(flt(progress) / total, 2)

			if self.percent_complete_method == "Task Weight" and total > 0:
				weight_sum = frappe.db.sql("""select sum(task_weight) from tabTask where project=%s""", self.name)[0][0]
				weighted_progress = frappe.db.sql("""select progress, task_weight from tabTask where project=%s""", self.name, as_dict=1)
				pct_complete = 0
				for row in weighted_progress:
					pct_complete += row["progress"] * frappe.utils.safe_div(row["task_weight"], weight_sum)
				self.percent_complete = flt(flt(pct_complete), 2)

		if update:
			self.db_set({
				'percent_complete': self.percent_complete,
			}, None, update_modified=update_modified)

	def set_ready_to_close(self, update=True, validate=True):
		previous_ready_to_close = cint(self.db_get("ready_to_close")) if not self.is_new() else 0
		self.ready_to_close = 1

		if validate:
			self.validate_on_ready_to_close()

		if not previous_ready_to_close:
			self.ready_to_close_dt = frappe.utils.now_datetime()

		self.status = "To Close"

		if update:
			self.db_set({
				'ready_to_close': self.ready_to_close,
				'ready_to_close_dt': self.ready_to_close_dt,
				'status': self.status,
			}, None)

		if self.ready_to_close != previous_ready_to_close:
			self.flags.status_changed = True

	def validate_on_ready_to_close(self):
		self.check_tasks_completed()
		self.check_insurance_details_on_ready_to_close()

	def check_tasks_completed(self):
		if not frappe.get_cached_value("Projects Settings", None, "validate_tasks_completed"):
			return

		incomplete_tasks = frappe.get_all("Task", filters={
			"project": self.name,
			"status": ["not in", ["Completed", "Cancelled"]]
		}, fields=["name", "subject"])

		if incomplete_tasks:
			frappe.throw(_("Task not completed:<br><br><ul>{0}</ul>").format(
				"".join([f"<li>{frappe.utils.get_link_to_form('Task', d.name)} ({d.subject})</li>" for d in incomplete_tasks])
			))

	def check_insurance_details_on_ready_to_close(self):
		if self.get('insurance_company') and not self.get('insurance_loss_no'):
			frappe.throw(_("Insurance Loss # is missing"))

	def reopen_status(self, update=True):
		self.ready_to_close = 0
		self.ready_to_close_dt = None
		self.status = "Open"

		if update:
			self.db_set({
				'ready_to_close': self.ready_to_close,
				'ready_to_close_dt': self.ready_to_close_dt,
				'status': self.status,
			}, None)

	def validate_for_transaction(self, doc):
		if doc.doctype == "Sales Invoice":
			self.check_is_ready_to_close()

	def check_is_ready_to_close(self):
		if not frappe.get_cached_value("Projects Settings", None, "validate_ready_to_close"):
			return

		if not self.ready_to_close:
			frappe.throw(_("{0} is not ready to close").format(frappe.get_desk_link(self.doctype, self.name)))

	def validate_project_status_for_transaction(self, doc):
		validate_project_status_for_transaction(self, doc)

	def set_status(self, update=False, status=None, update_modified=True, reset=False):
		if self.is_new():
			previous_status, previous_project_status, previous_indicator_color = self.status, self.project_status, self.indicator_color
		else:
			previous_status, previous_project_status, previous_indicator_color = self.db_get(["status", "project_status", "indicator_color"])

		# set/reset manual status
		if reset:
			self.project_status = None
		elif status:
			set_manual_project_status(self, status)

		# get evaulated status
		project_status = get_auto_project_status(self)

		# no applicable status
		if not project_status:
			if self.status != previous_status:
				self.flags.status_changed = True
			if update:
				self.handle_on_status_change()

			return

		# set status
		self.project_status = project_status.name
		self.status = project_status.status
		self.indicator_color = project_status.indicator_color
		self.show_task_type = project_status.show_task_type

		# status comment only if project status changed
		if not self.is_new() and self.project_status != previous_project_status:
			self.add_comment("Label", _(self.project_status))

		if self.status != previous_status:
			self.flags.status_changed = True

		# update database only if changed
		if update:
			if (
				self.project_status != previous_project_status
				or self.status != previous_status
				or cstr(self.indicator_color) != cstr(previous_indicator_color)
			):
				self.db_set({
					'project_status': self.project_status,
					'status': self.status,
					'indicator_color': self.indicator_color,
					'show_task_type': self.show_task_type,
				}, None, update_modified=update_modified)

			# Only run after updating directly in db
			self.handle_on_status_change()

	def validate_cant_change(self):
		if self.is_new():
			return

		fields = self.get_cant_change_fields()
		cant_change_fields = [f for f, cant_change in fields.items() if cant_change and self.meta.get_field(f) and self.meta.get_field(f).fieldtype != 'Table']

		if cant_change_fields:
			previous_values = frappe.db.get_value(self.doctype, self.name, cant_change_fields, as_dict=1)
			for f, old_value in previous_values.items():
				if cstr(self.get(f)) != cstr(old_value):
					label = self.meta.get_label(f)
					frappe.throw(_("Cannot change {0} because transactions already exist against this Project")
						.format(frappe.bold(label)))

	def get_cant_change_fields(self):
		has_sales_transaction = self.has_sales_transaction()
		has_billable_transaction = self.has_billable_transaction()

		return frappe._dict({
			'customer': has_sales_transaction,
			'bill_to': self.is_warranty_claim and has_billable_transaction,
			'is_warranty_claim': self.is_warranty_claim and has_billable_transaction,
		})

	def has_sales_transaction(self):
		if getattr(self, '_has_sales_transaction', None):
			return self._has_sales_transaction

		if frappe.db.get_value("Sales Order", {'project': self.name, 'docstatus': 1})\
				or frappe.db.get_value("Sales Invoice", {'project': self.name, 'docstatus': 1})\
				or frappe.db.get_value("Delivery Note", {'project': self.name, 'docstatus': 1})\
				or frappe.db.get_value("Quotation", {'project': self.name, 'docstatus': 1}):
			self._has_sales_transaction = True
		else:
			self._has_sales_transaction = False

		return self._has_sales_transaction

	def has_billable_transaction(self):
		if getattr(self, '_has_billable_transaction', None):
			return self._has_billable_transaction

		has_billable_sales_order = frappe.db.get_value("Sales Order", {'project': self.name, 'docstatus': 1,
			'per_returned': ['<', 100]})
		has_billable_delivery_note = frappe.db.get_value("Delivery Note", {'project': self.name, 'docstatus': 1,
			'is_return': 0, 'per_returned': ['<', 100]})

		if has_billable_sales_order or has_billable_delivery_note:
			self._has_billable_transaction = True
		else:
			self._has_billable_transaction = False

		return self._has_billable_transaction

	def validate_project_type(self):
		if self.status in ['Completed', 'Closed']:
			return

		if self.project_type:
			project_type = frappe.get_cached_doc("Project Type", self.project_type)

			if project_type.bill_to_mandatory and not self.get('bill_to'):
				frappe.throw(_("Bill To is mandatory for Project Type {0}").format(self.project_type))

			if project_type.insurance_company_mandatory and not self.get('insurance_company'):
				frappe.throw(_("Insurance Company is mandatory for Project Type {0}").format(self.project_type))

			if project_type.campaign_mandatory and not self.get('campaign'):
				frappe.throw(_("Campaign is mandatory for Project Type {0}").format(self.project_type))

			if project_type.previous_project_mandatory and not self.get('previous_project'):
				frappe.throw(_("{0} is mandatory for Project Type {1}")
					.format(self.meta.get_label('previous_project'), self.project_type))

	def validate_cash_billing(self):
		bill_to = self.bill_to or self.customer
		cash_billing = frappe.get_cached_value("Customer", bill_to, "cash_billing")
		if cash_billing:
			self.cash_billing = 1

	def validate_appointment_required(self):
		if self.get('appointment'):
			return

		project_type = frappe.get_cached_doc("Project Type", self.project_type)
		appointment_required = project_type.is_internal != "Yes" and frappe.get_cached_value("Projects Settings", None, "appointment_required")
		appointment_bypassed = self.project_type and frappe.get_cached_value("Project Type", self.project_type, "appointment_not_required")

		if appointment_required and not appointment_bypassed:
			frappe.throw(_("Appointment is mandatory, please select an Appointment first"))

	def validate_appointment(self):
		if self.get('appointment'):
			appointment_details = frappe.db.get_value("Appointment", self.appointment,
				['name', 'status', 'docstatus'], as_dict=1)

			if not appointment_details:
				frappe.throw(_("Appointment {0} does not exist").format(self.appointment))

			if appointment_details.docstatus == 0:
				frappe.throw(_("{0} is not submitted").format(frappe.get_desk_link("Appointment", self.appointment)))
			if appointment_details.docstatus == 2:
				frappe.throw(_("{0} is cancelled").format(frappe.get_desk_link("Appointment", self.appointment)))
			if appointment_details.status == "Rescheduled":
				frappe.throw(_("{0} is {1}. Please select newer appointment instead")
					.format(frappe.get_desk_link("Appointment", self.appointment), frappe.bold(appointment_details.status)))

	def update_appointment(self):
		appointments = []
		if self.appointment:
			appointments.append(self.appointment)

		previous_appointment = self.get('_previous_appointment')
		if previous_appointment and previous_appointment not in appointments:
			appointments.append(previous_appointment)

		for appointment in appointments:
			doc = frappe.get_doc("Appointment", appointment)
			doc.set_status(update=True)
			doc.notify_update()

	def validate_phone_nos(self):
		if not self.get('contact_mobile') and self.get('contact_mobile_2'):
			self.contact_mobile = self.contact_mobile_2
			self.contact_mobile_2 = ''
		if self.get('contact_mobile') == self.get('contact_mobile_2'):
			self.contact_mobile_2 = ''

	def set_missing_values(self):
		self.set_appointment_details()
		self.set_customer_details()
		self.set_applies_to_details()
		self.set_project_template_details()
		self.set_material_and_service_item_groups()

	def set_customer_details(self):
		args = self.as_dict()

		customer_details = get_customer_details(args)
		for k, v in customer_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in self.force_customer_fields:
				self.set(k, v)

		bill_to_details = get_bill_to_details(args)
		for k, v in bill_to_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in self.force_customer_fields:
				self.set(k, v)

	@frappe.whitelist()
	def set_applies_to_details(self):
		args = self.as_dict()
		applies_to_details = get_applies_to_details(args, for_validate=True)

		for k, v in applies_to_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in self.force_applies_to_fields:
				self.set(k, v)

	def get_checklist_rows(self, parentfield, rows=1):
		checklist = self.get(parentfield) or []
		per_row = ceil(len(checklist) / rows)

		out = []
		for i in range(rows):
			out.append([])

		for i, d in enumerate(checklist):
			row_id = i // per_row
			out[row_id].append(d)

		return out

	def set_project_template_details(self):
		for d in self.project_templates:
			if d.project_template and not d.project_template_name:
				d.project_template_name = frappe.get_cached_value("Project Template", d.project_template, "project_template_name")

	def set_appointment_details(self):
		if self.appointment:
			appointment_doc = frappe.get_doc("Appointment", self.appointment)

			self.appointment_dt = appointment_doc.scheduled_dt

			if not self.customer:
				customer = appointment_doc.get_customer()
				if customer:
					self.customer = customer
		else:
			self.appointment_dt = None

	def set_material_and_service_item_groups(self):
		settings = frappe.get_cached_doc("Projects Settings", None)
		self.materials_item_group = settings.materials_item_group
		self.lubricants_item_group = settings.lubricants_item_group
		self.sublet_item_group = settings.sublet_item_group

	def validate_readings(self):
		if self.meta.has_field('fuel_level'):
			if flt(self.fuel_level) < 0 or flt(self.fuel_level) > 100:
				frappe.throw(_("Fuel Level must be between 0% and 100%"))
		if self.meta.has_field('keys'):
			if cint(self.keys) < 0:
				frappe.throw(_("No of Keys cannot be negative"))

	def set_project_in_sales_order_and_quotation(self):
		if self.sales_order:
			frappe.db.set_value("Sales Order", self.sales_order, "project", self.name, notify=1)

			quotations = frappe.db.sql_list("""
				select distinct qtn.name
				from `tabQuotation` qtn
				inner join `tabSales Order Item` item on item.quotation = qtn.name
				where item.parent = %s and qtn.docstatus < 2 and ifnull(qtn.project, '') = ''
			""", self.sales_order)

			for quotation in quotations:
				frappe.db.set_value("Quotation", quotation, "project", self.name, notify=1)

	def validate_depreciation(self):
		if not self.insurance_company:
			self.default_depreciation_percentage = 0
			self.default_underinsurance_percentage = 0
			self.non_standard_depreciation = []
			self.non_standard_underinsurance = []
			return

		if flt(self.default_depreciation_percentage) > 100:
			frappe.throw(_("Default Depreciation Rate cannot be greater than 100%"))
		elif flt(self.default_depreciation_percentage) < 0:
			frappe.throw(_("Default Depreciation Rate cannot be negative"))

		if flt(self.default_underinsurance_percentage) > 100:
			frappe.throw(_("Default Underinsurance Rate cannot be greater than 100%"))
		elif flt(self.default_underinsurance_percentage) < 0:
			frappe.throw(_("Default Underinsurance Rate cannot be negative"))

		item_codes_visited = set()
		for d in self.non_standard_depreciation:
			if flt(d.depreciation_percentage) > 100:
				frappe.throw(_("Row #{0}: Depreciation Rate cannot be greater than 100%").format(d.idx))
			elif flt(d.depreciation_percentage) < 0:
				frappe.throw(_("Row #{0}: Depreciation Rate cannot be negative").format(d.idx))

			if d.depreciation_item_code in item_codes_visited:
				frappe.throw(_("Row #{0}: Duplicate Non Standard Depreciation row for Item {1}")
					.format(d.idx, frappe.bold(d.depreciation_item_code)))

		item_codes_visited = set()
		for d in self.non_standard_underinsurance:
			if flt(d.underinsurance_percentage) > 100:
				frappe.throw(_("Row #{0}: Underinsurance Rate cannot be greater than 100%").format(d.idx))
			elif flt(d.underinsurance_percentage) < 0:
				frappe.throw(_("Row #{0}: Underinsurance Rate cannot be negative").format(d.idx))

			if d.underinsurance_item_code in item_codes_visited:
				frappe.throw(_("Row #{0}: Duplicate Non Standard Underinsurance row for Item {1}")
					.format(d.idx, frappe.bold(d.underinsurance_item_code)))

			item_codes_visited.add(d.underinsurance_item_code)

	def validate_warranty(self):
		if self.get('warranty_claim_denied'):
			self.warranty_claim_denied_reason = clean_whitespace(self.warranty_claim_denied_reason)
			if not self.warranty_claim_denied_reason:
				frappe.throw(_("Warranty Claim Denied Reason is mandatory for setting as Denied"))
		else:
			self.warranty_claim_denied_reason = None

	def copy_from_template(self):
		'''
		Copy tasks from template
		'''
		if self.project_template and not frappe.db.get_all('Task', dict(project = self.name), limit=1):

			# has a template, and no loaded tasks, so lets create
			if not self.expected_start_date:
				# project starts today
				self.expected_start_date = today()

			template = frappe.get_doc('Project Template', self.project_template)

			if not self.project_type:
				self.project_type = template.project_type

			# create tasks from template
			for task in template.tasks:
				frappe.get_doc(dict(
					doctype = 'Task',
					subject = task.subject,
					project = self.name,
					status = 'Open',
					exp_start_date = add_days(self.expected_start_date, task.start),
					exp_end_date = add_days(self.expected_start_date, task.start + task.duration),
					description = task.description,
					task_weight = task.task_weight
				)).insert()

	def set_items_and_totals_html_onload(self, sales_data, consumables_data):
		currency = erpnext.get_company_currency(self.company)

		service_items_html = frappe.render_template("erpnext/projects/doctype/project/project_items_table.html", {
			"title": _("Service Sales"),
			"doc": self,
			"data": sales_data.service_items,
			"currency": currency,
			"show_sales_order": True,
			"show_amount": True,
		})

		material_items_html = frappe.render_template("erpnext/projects/doctype/project/project_items_table.html", {
			"title": _("Material Sales"),
			"doc": self,
			"data": sales_data.material_items,
			"currency": currency,
			"show_sales_order": True,
			"show_delivery_note": True,
			"show_amount": True,
		})

		consumable_items_html = frappe.render_template("erpnext/projects/doctype/project/project_items_table.html", {
			"title": _("Consumables"),
			"doc": self,
			"data": consumables_data,
			"currency": currency,
			"show_material_request": True,
			"show_stock_entry": True,
		})

		sales_summary_html = frappe.render_template("erpnext/projects/doctype/project/project_sales_summary.html",
			{"doc": self, "currency": currency})

		self.set_onload('service_items_html', service_items_html)
		self.set_onload('material_items_html', material_items_html)
		self.set_onload('consumable_items_html', consumable_items_html)
		self.set_onload('sales_summary_html', sales_summary_html)

	def get_project_sales_data(self, get_sales_invoice=True):
		sales_data = frappe._dict()
		sales_data.material_items, sales_data.part_items, sales_data.lubricant_items = get_material_items(self,
			get_sales_invoice=get_sales_invoice)
		sales_data.service_items, sales_data.labour_items, sales_data.sublet_items = get_service_items(self,
			get_sales_invoice=get_sales_invoice)
		sales_data.totals = get_totals_data([sales_data.material_items, sales_data.service_items], self.company)

		return sales_data

	def get_project_consumables_data(self):
		return get_consumable_items(self)

	def get_sales_invoices(self, exclude_indirect_invoice=False):
		if exclude_indirect_invoice:
			project_condition = "inv.project = %(project)s"
		else:
			project_condition = """inv.project = %(project)s or exists(
				select item.name from `tabSales Invoice Item` item
				where item.parent = inv.name and item.project = %(project)s)"""

		return frappe.db.sql("""
			select inv.name, inv.customer, inv.bill_to
			from `tabSales Invoice` inv
			where inv.docstatus = 1 and ({0})
			order by posting_date, posting_time, creation
		""".format(project_condition), {'project': self.name}, as_dict=1)

	def get_sales_invoice_names(self):
		# Invoices
		invoices = self.get_sales_invoices()
		self.invoices = [d.name for d in invoices]

	def get_activity_summary(self):
		return frappe.db.sql("""
			select activity_type, sum(hours) as total_hours
			from `tabTimesheet Detail`
			where project=%s and docstatus < 2
			group by activity_type
			order by total_hours desc
		""", self.name, as_dict=True)

	def set_project_date(self):
		self.project_date = getdate(
			self.expected_start_date
			or self.creation
		)

	def after_rename(self, old_name, new_name, merge=False):
		if old_name == self.copied_from:
			frappe.db.set_value('Project', new_name, 'copied_from', new_name)

	def get_item_groups_subtree(self, item_group):
		if (self.get('_item_group_subtree') or {}).get(item_group):
			return self._item_group_subtree[item_group]

		item_group_tree = []
		if item_group:
			item_group_tree = frappe.get_all("Item Group", {"name": ["subtree of", item_group]})
			item_group_tree = [d.name for d in item_group_tree]

		if not self.get('_item_group_subtree'):
			self._item_group_subtree = {}

		self._item_group_subtree[item_group] = item_group_tree

		return self._item_group_subtree[item_group]


def get_material_items(project, get_sales_invoice=True):
	is_material_condition = "i.is_stock_item = 1"
	materials_item_groups = project.get_item_groups_subtree(project.materials_item_group)
	if materials_item_groups:
		is_material_condition = "(i.is_stock_item = 1 or i.item_group in ({0}))"\
			.format(", ".join([frappe.db.escape(d) for d in materials_item_groups]))

	dn_data = frappe.db.sql("""
		select p.name as delivery_note, i.sales_order,
			p.posting_date, p.posting_time, i.idx,
			i.item_code, i.item_name, i.description, i.item_group, i.is_stock_item,
			i.qty, i.uom,
			i.base_net_amount as net_amount,
			i.base_net_rate as net_rate,
			i.base_taxable_amount as taxable_amount,
			i.base_total_discount as total_discount,
			i.item_tax_detail, i.claim_customer, p.conversion_rate
		from `tabDelivery Note Item` i
		inner join `tabDelivery Note` p on p.name = i.parent
		where p.docstatus = 1 and {0}
			and p.project = %s
	""".format(is_material_condition), project.name, as_dict=1)
	set_sales_data_customer_amounts(dn_data, project)

	so_data = frappe.db.sql("""
		select p.name as sales_order,
			p.transaction_date, i.idx,
			i.item_code, i.item_name, i.description, i.item_group, i.is_stock_item,
			if(i.is_stock_item = 1, i.qty - i.delivered_qty, i.qty) as qty,
			i.qty as ordered_qty,
			i.delivered_qty,
			i.uom,
			if(i.is_stock_item = 1, i.base_net_amount * (i.qty - i.delivered_qty) / i.qty, i.base_net_amount) as net_amount,
			i.base_net_rate as net_rate,
			if(i.is_stock_item = 1, i.base_taxable_amount * (i.qty - i.delivered_qty) / i.qty, i.base_taxable_amount) as taxable_amount,
			if(i.is_stock_item = 1, i.base_total_discount * (i.qty - i.delivered_qty) / i.qty, i.base_total_discount) as total_discount,
			i.item_tax_detail, i.claim_customer, p.conversion_rate
		from `tabSales Order Item` i
		inner join `tabSales Order` p on p.name = i.parent
		where p.docstatus = 1 and {0}
			and (i.delivered_qty < i.qty or i.is_stock_item = 0)
			and i.qty > 0
			and (p.status != 'Closed' or exists(select sum(si_item.amount)
				from `tabSales Invoice Item` si_item
				where si_item.docstatus = 1 and si_item.sales_order_item = i.name and ifnull(si_item.delivery_note, '') = ''
				having sum(si_item.amount) > 0)
			)
			and p.project = %s
	""".format(is_material_condition), project.name, as_dict=1)
	set_sales_data_customer_amounts(so_data, project)

	sinv_data = frappe.db.sql("""
		select p.name as sales_invoice, i.delivery_note, i.sales_order,
			p.posting_date, p.posting_time, i.idx,
			i.item_code, i.item_name, i.description, i.item_group, i.is_stock_item,
			i.qty, i.uom,
			i.base_net_amount as net_amount,
			i.base_net_rate as net_rate,
			i.base_taxable_amount as taxable_amount,
			i.base_total_discount as total_discount,
			i.item_tax_detail, p.conversion_rate
		from `tabSales Invoice Item` i
		inner join `tabSales Invoice` p on p.name = i.parent
		where p.docstatus = 1 and {0} and ifnull(i.sales_order, '') = '' and ifnull(i.delivery_note, '') = ''
			and i.project = %s
	""".format(is_material_condition), project.name, as_dict=1)
	set_sales_data_customer_amounts(sinv_data, project)

	materials_data = get_items_data_template()
	parts_data = get_items_data_template()
	lubricants_data = get_items_data_template()

	lubricants_item_groups = project.get_item_groups_subtree(project.lubricants_item_group)
	for d in dn_data + so_data + sinv_data:
		materials_data['items'].append(d)

		if d.item_group in lubricants_item_groups:
			lubricants_data['items'].append(d.copy())
		else:
			parts_data['items'].append(d.copy())

	materials_data['items'] = sorted(materials_data['items'], key=lambda d: (cstr(d.posting_date), cstr(d.posting_time), d.idx))
	parts_data['items'] = sorted(parts_data['items'], key=lambda d: (cstr(d.posting_date), cstr(d.posting_time), d.idx))
	lubricants_data['items'] = sorted(lubricants_data['items'], key=lambda d: (cstr(d.posting_date), cstr(d.posting_time), d.idx))

	get_item_taxes(project, materials_data, project.company)
	post_process_items_data(materials_data)

	get_item_taxes(project, parts_data, project.company)
	post_process_items_data(parts_data)

	get_item_taxes(project, lubricants_data, project.company)
	post_process_items_data(lubricants_data)

	return materials_data, parts_data, lubricants_data


def get_service_items(project, get_sales_invoice=True):
	is_service_condition = "(i.is_stock_item = 0 and i.is_fixed_asset = 0)"
	materials_item_groups = project.get_item_groups_subtree(project.materials_item_group)
	if materials_item_groups:
		is_service_condition = "(i.is_stock_item = 0 and i.is_fixed_asset = 0 and i.item_group not in ({0}))"\
			.format(", ".join([frappe.db.escape(d) for d in materials_item_groups]))

	so_data = frappe.db.sql("""
		select p.name as sales_order,
			p.transaction_date,
			i.item_code, i.item_name, i.description, i.item_group, i.is_stock_item,
			i.qty, i.uom,
			i.base_net_amount as net_amount,
			i.base_net_rate as net_rate,
			i.base_taxable_amount as taxable_amount,
			i.base_total_discount as total_discount,
			i.item_tax_detail, i.claim_customer, p.conversion_rate
		from `tabSales Order Item` i
		inner join `tabSales Order` p on p.name = i.parent
		where p.docstatus = 1 and {0}
			and p.project = %s
			and (p.status != 'Closed' or exists(select sum(si_item.amount)
				from `tabSales Invoice Item` si_item
				where si_item.docstatus = 1 and si_item.sales_order_item = i.name
				having sum(si_item.amount) > 0)
			)
		order by p.transaction_date, p.creation, i.idx
	""".format(is_service_condition), project.name, as_dict=1)
	set_sales_data_customer_amounts(so_data, project)

	sinv_data = []
	if get_sales_invoice:
		sinv_data = frappe.db.sql("""
			select p.name as sales_invoice, i.delivery_note, i.sales_order,
				p.posting_date as transaction_date,
				i.item_code, i.item_name, i.description, i.item_group, i.is_stock_item,
				i.qty, i.uom,
				i.base_net_amount as net_amount,
				i.base_net_rate as net_rate,
				i.base_taxable_amount as taxable_amount,
				i.base_total_discount as total_discount,
				i.item_tax_detail, p.conversion_rate
			from `tabSales Invoice Item` i
			inner join `tabSales Invoice` p on p.name = i.parent
			where p.docstatus = 1 and {0} and ifnull(i.sales_order, '') = ''
				and i.project = %s
			order by p.posting_date, p.creation, i.idx
		""".format(is_service_condition), project.name, as_dict=1)
	set_sales_data_customer_amounts(sinv_data, project)

	service_data = get_items_data_template()
	labour_data = get_items_data_template()
	sublet_data = get_items_data_template()

	sublet_item_groups = project.get_item_groups_subtree(project.sublet_item_group)
	for d in so_data + sinv_data:
		service_data['items'].append(d)

		if d.item_group in sublet_item_groups:
			sublet_data['items'].append(d.copy())
		else:
			labour_data['items'].append(d.copy())

	get_item_taxes(project, service_data, project.company)
	post_process_items_data(service_data)

	get_item_taxes(project, labour_data, project.company)
	post_process_items_data(labour_data)

	get_item_taxes(project, sublet_data, project.company)
	post_process_items_data(sublet_data)

	return service_data, labour_data, sublet_data


def get_consumable_items(project):
	ste_data = frappe.db.sql("""
		select p.name as stock_entry, p.purpose, i.material_request,
			p.posting_date, p.posting_time, i.idx,
			i.item_code, i.item_name, i.description, i.item_group,
			i.qty, i.uom
		from `tabStock Entry Detail` i
		inner join `tabStock Entry` p on p.name = i.parent
		where p.docstatus = 1 and p.project = %s and p.purpose in ('Material Issue', 'Material Receipt')
		order by p.posting_date, p.creation, i.idx
	""", project.name, as_dict=1)

	mreq_data = frappe.db.sql("""
		select p.name as material_request,
			p.transaction_date, i.idx,
			i.item_code, i.item_name, i.description, i.item_group,
			(i.stock_qty - i.received_qty) / i.conversion_factor as qty,
			i.qty as requested_qty,
			i.received_qty,
			i.uom
		from `tabMaterial Request Item` i
		inner join `tabMaterial Request` p on p.name = i.parent
		where p.docstatus = 1
			and p.material_request_type = 'Material Issue'
			and i.received_qty < i.stock_qty
			and p.status != 'Stopped'
			and p.project = %s
		order by p.transaction_date, p.creation, i.idx
	""", project.name, as_dict=1)

	consumables_data = frappe._dict({'total_qty': 0, 'items': []})

	for d in ste_data:
		if d.purpose == "Material Receipt":
			d.qty *= -1

	for d in ste_data + mreq_data:
		consumables_data['items'].append(d)

	consumables_data['items'] = sorted(consumables_data['items'], key=lambda d: (cstr(d.posting_date), cstr(d.posting_time), d.idx))
	for i, d in enumerate(consumables_data['items']):
		d.idx = i + 1
		consumables_data.total_qty += d.qty

	return consumables_data


def get_items_data_template():
	return frappe._dict({
		'total_qty': 0,

		'net_total': 0,
		'customer_net_total': 0,

		'taxable_total': 0,

		'sales_taxable_total': 0,
		'sales_tax_total': 0,
		'customer_sales_tax_total': 0,

		'service_taxable_total': 0,
		'service_tax_total': 0,
		'customer_service_tax_total': 0,

		'other_taxes_and_charges': 0,
		'customer_other_taxes_and_charges': 0,

		'taxes': {},
		'customer_taxes': {},

		'items': [],
	})


def set_sales_data_customer_amounts(data, project):
	set_depreciation_in_invoice_items(data, project, force=True)

	for d in data:
		d.has_customer_depreciation = 0

		if d.get('claim_customer') and project.customer and d.get('claim_customer') != project.customer:
			d.is_claim_item = 1

			if d.total_discount:
				d.customer_net_amount = d.net_amount
				d.customer_net_rate = d.net_rate
				d.net_amount = d.customer_net_amount + d.total_discount
				d.net_rate = d.net_amount / d.qty if d.qty else d.net_amount
			else:
				d.customer_net_amount = 0
				d.customer_net_rate = 0
		else:
			d.is_claim_item = 0

			if project.insurance_company and project.bill_to and project.bill_to != project.customer:
				d.has_customer_depreciation = 1

				depreciation_amount = d.net_amount * flt(d.depreciation_percentage) / 100
				underinsurance_amount = (d.net_amount - depreciation_amount) * flt(d.underinsurance_percentage) / 100
				d.customer_net_amount = depreciation_amount + underinsurance_amount

				depreciation_rate = d.net_rate * flt(d.depreciation_percentage) / 100
				underinsurance_rate = (d.net_rate - depreciation_rate) * flt(d.underinsurance_percentage) / 100
				d.customer_net_rate = depreciation_rate + underinsurance_rate

				d.cumulative_depreciation_percentage = d.customer_net_amount / d.net_amount * 100 if d.net_amount else 0
			else:
				d.customer_net_amount = d.net_amount
				d.customer_net_rate = d.net_rate


def get_item_taxes(project, data, company):
	sales_tax_account = frappe.get_cached_value('Company', company, "sales_tax_account")
	service_tax_account = frappe.get_cached_value('Company', company, "service_tax_account")

	for d in data['items']:
		conversion_rate = flt(d.get('conversion_rate')) or 1

		d.setdefault('taxes', {})
		d.setdefault('customer_taxes', {})

		d.setdefault('sales_tax_amount', 0)
		d.setdefault('customer_sales_tax_amount', 0)

		d.setdefault('service_tax_amount', 0)
		d.setdefault('customer_service_tax_amount', 0)

		d.setdefault('other_taxes_and_charges', 0)
		d.setdefault('customer_other_taxes_and_charges', 0)

		if project.get('has_stin'):
			item_tax_detail = json.loads(d.item_tax_detail or '{}')
			for tax_row_name, amount in item_tax_detail.items():
				tax_account = frappe.db.get_value("Sales Taxes and Charges", tax_row_name, 'account_head', cache=1)
				if tax_account:
					tax_amount = flt(amount)
					tax_amount *= conversion_rate

					customer_tax_amount = 0 if d.get('is_claim_item') and not d.get('total_discount') else flt(amount)
					if d.has_customer_depreciation:
						customer_tax_amount *= d.cumulative_depreciation_percentage / 100

					customer_tax_amount *= conversion_rate

					if flt(d.ordered_qty):
						tax_amount = tax_amount * flt(d.qty) / flt(d.ordered_qty)
						customer_tax_amount = customer_tax_amount * flt(d.qty) / flt(d.ordered_qty)

					d.taxes.setdefault(tax_account, 0)
					d.taxes[tax_account] += tax_amount

					d.customer_taxes.setdefault(tax_account, 0)
					d.customer_taxes[tax_account] += customer_tax_amount

					if tax_account == sales_tax_account:
						d.sales_tax_amount += tax_amount
						d.customer_sales_tax_amount += customer_tax_amount
					elif tax_account == service_tax_account:
						d.service_tax_amount += tax_amount
						d.customer_service_tax_amount += customer_tax_amount
					else:
						d.other_taxes_and_charges += tax_amount
						d.customer_other_taxes_and_charges += customer_tax_amount


def post_process_items_data(data):
	for i, d in enumerate(data['items']):
		d.idx = i + 1

		data.total_qty += flt(d.qty)

		data.net_total += flt(d.net_amount)
		data.customer_net_total += flt(d.customer_net_amount)

		data.taxable_total += flt(d.taxable_amount)
		if flt(d.sales_tax_amount):
			data.sales_taxable_total += flt(d.taxable_amount)
		if flt(d.service_tax_amount):
			data.service_taxable_total += flt(d.taxable_amount)

		data.sales_tax_total += flt(d.sales_tax_amount)
		data.customer_sales_tax_total += flt(d.customer_sales_tax_amount)

		data.service_tax_total += flt(d.service_tax_amount)
		data.customer_service_tax_total += flt(d.customer_service_tax_amount)

		data.other_taxes_and_charges += flt(d.other_taxes_and_charges)
		data.customer_other_taxes_and_charges += flt(d.customer_other_taxes_and_charges)

		for tax_account, tax_amount in d.taxes.items():
			data.taxes.setdefault(tax_account, 0)
			data.taxes[tax_account] += tax_amount
		for tax_account, tax_amount in d.customer_taxes.items():
			data.customer_taxes.setdefault(tax_account, 0)
			data.customer_taxes[tax_account] += tax_amount

	data.sales_tax_rate = data.sales_tax_total / data.sales_taxable_total * 100 if data.sales_taxable_total else 0
	data.service_tax_rate = data.service_tax_total / data.service_taxable_total * 100 if data.service_taxable_total else 0


def get_totals_data(items_dataset, company):
	totals_data = frappe._dict({
		'taxes': {},
		'customer_taxes': {},

		'sales_tax_total': 0,
		'customer_sales_tax_total': 0,

		'service_tax_total': 0,
		'customer_service_tax_total': 0,

		'other_taxes_and_charges': 0,
		'customer_other_taxes_and_charges': 0,

		'total_taxes_and_charges': 0,
		'customer_total_taxes_and_charges': 0,

		'net_total': 0,
		'customer_net_total': 0,

		'taxable_total': 0,
		'sales_taxable_total': 0,
		'service_taxable_total': 0,

		'sales_tax_rate': 0,
		'service_tax_rate': 0,

		'grand_total': 0,
		'customer_grand_total': 0,
	})
	for data in items_dataset:
		totals_data.net_total += flt(data.net_total)
		totals_data.customer_net_total += flt(data.customer_net_total)

		totals_data.taxable_total += flt(data.taxable_total)
		totals_data.sales_taxable_total += flt(data.sales_taxable_total)
		totals_data.service_taxable_total += flt(data.service_taxable_total)

		totals_data.sales_tax_total += flt(data.sales_tax_total)
		totals_data.customer_sales_tax_total += flt(data.customer_sales_tax_total)

		totals_data.service_tax_total += flt(data.service_tax_total)
		totals_data.customer_service_tax_total += flt(data.customer_service_tax_total)

		totals_data.other_taxes_and_charges += flt(data.other_taxes_and_charges)
		totals_data.customer_other_taxes_and_charges += flt(data.customer_other_taxes_and_charges)

		for tax_account, tax_amount in data.taxes.items():
			totals_data.taxes.setdefault(tax_account, 0)
			totals_data.taxes[tax_account] += tax_amount
			totals_data.total_taxes_and_charges += tax_amount

		for tax_account, tax_amount in data.customer_taxes.items():
			totals_data.customer_taxes.setdefault(tax_account, 0)
			totals_data.customer_taxes[tax_account] += tax_amount
			totals_data.customer_total_taxes_and_charges += tax_amount

	totals_data.grand_total += totals_data.net_total + totals_data.total_taxes_and_charges
	totals_data.customer_grand_total += totals_data.customer_net_total + totals_data.customer_total_taxes_and_charges

	totals_data.sales_tax_rate = totals_data.sales_tax_total / totals_data.sales_taxable_total * 100\
		if totals_data.sales_taxable_total else 0
	totals_data.service_tax_rate = totals_data.service_tax_total / totals_data.service_taxable_total * 100\
		if totals_data.service_taxable_total else 0

	# Round Grand Totals
	grand_total_precision = get_field_precision(frappe.get_meta("Sales Invoice").get_field("grand_total"),
		currency=frappe.get_cached_value('Company', company, "default_currency"))
	totals_data.grand_total = flt(totals_data.grand_total, grand_total_precision)
	totals_data.customer_grand_total = flt(totals_data.customer_grand_total, grand_total_precision)

	return totals_data


def get_timeline_data(doctype, name):
	'''Return timeline for attendance'''
	return dict(frappe.db.sql('''select unix_timestamp(from_time), count(*)
		from `tabTimesheet Detail` where project=%s
			and from_time > date_sub(curdate(), interval 1 year)
			and docstatus < 2
			group by date(from_time)''', name))


@frappe.whitelist()
def create_kanban_board_if_not_exists(project):
	from frappe.desk.doctype.kanban_board.kanban_board import quick_kanban_board

	if not frappe.db.exists('Kanban Board', project):
		quick_kanban_board('Task', project, 'status')

	return True


@frappe.whitelist()
def set_project_ready_to_close(project):
	project = frappe.get_doc('Project', project)
	project.check_permission('write')

	project.set_ready_to_close(update=True)
	project.set_status(update=True)
	project.notify_update()


@frappe.whitelist()
def reopen_project_status(project):
	project = frappe.get_doc('Project', project)
	project.check_permission('write')

	project.reopen_status(update=True)
	project.set_status(update=True, reset=True)
	project.notify_update()


@frappe.whitelist()
def set_project_status(project, project_status):
	project = frappe.get_doc('Project', project)
	project.check_permission('write')

	project.set_status(status=project_status)
	project.save()


@frappe.whitelist()
def get_customer_details(args):
	if isinstance(args, str):
		args = json.loads(args)

	args = frappe._dict(args)
	out = frappe._dict()

	customer = frappe._dict()
	if args.customer:
		customer = frappe.get_cached_doc("Customer", args.customer)

	out.customer_name = customer.customer_name
	out.customer_group = customer.customer_group

	# Tax IDs
	out.tax_id = customer.tax_id
	out.tax_cnic = customer.tax_cnic
	out.tax_strn = customer.tax_strn
	out.tax_status = customer.tax_status

	# Customer Address
	out.customer_address = args.customer_address
	if not out.customer_address and customer.name:
		out.customer_address = get_default_address("Customer", customer.name)

	out.address_display = get_address_display(out.customer_address)

	# Contact
	out.contact_person = args.contact_person
	if not out.contact_person and customer.name:
		out.contact_person = get_default_contact("Customer", customer.name)

	out.update(get_contact_details(out.contact_person))

	out.secondary_contact_person = args.secondary_contact_person
	secondary_contact_details = get_contact_details(out.secondary_contact_person)
	secondary_contact_details = {"secondary_" + k: v for k, v in secondary_contact_details.items()}
	out.update(secondary_contact_details)

	out.contact_nos = get_all_contact_nos("Customer", customer.name)

	return out


@frappe.whitelist()
def get_bill_to_details(args):
	if isinstance(args, str):
		args = json.loads(args)

	args = frappe._dict(args)
	out = frappe._dict()

	bill_to = frappe._dict()
	if args.bill_to:
		bill_to = frappe.get_cached_doc("Customer", args.bill_to)

	out.bill_to_name = bill_to.customer_name
	out.bill_to_customer_group = bill_to.customer_group

	return out


@frappe.whitelist()
def get_project_details(project, doctype, purpose=None):
	from erpnext.controllers.transaction_controller import is_doctype_selling_or_buying

	if isinstance(project, str):
		project = frappe.get_doc("Project", project)

	is_sales_doctype = is_doctype_selling_or_buying(doctype) == "selling"

	out = frappe._dict()
	out['project_reference_no'] = project.get('reference_no')

	fieldnames = [
		'company', 'branch',
		'customer', 'bill_to',
		'contact_person', 'contact_mobile', 'contact_phone',
		'applies_to_item', 'applies_to_serial_no',
		'service_advisor',
		'insurance_company', 'insurance_loss_no', 'insurance_policy_no',
		'insurance_surveyor', 'insurance_surveyor_company',
		'has_stin', 'default_depreciation_percentage', 'default_underinsurance_percentage',
		'campaign', 'po_no', 'po_date', 'cost_center',
	]
	sales_only_fields = [
		'customer', 'bill_to', 'has_stin',
		'default_depreciation_percentage', 'default_underinsurance_percentage',
		'contact_person', 'contact_mobile', 'contact_phone',
		'po_no', 'po_date',
	]
	ignore_empty_fields = ['customer', 'bill_to', 'po_no', 'po_date']

	force_fields = []
	if doctype == "Material Request":
		force_fields.append("customer")

	for f in fieldnames:
		if f in sales_only_fields and not is_sales_doctype and f not in force_fields:
			continue
		if f in ignore_empty_fields and not project.get(f):
			continue

		out[f] = project.get(f)

		if doctype == "Quotation" and f == 'customer':
			out['quotation_to'] = 'Customer'
			out['party_name'] = project.get(f)

	default_warehouse = project.default_warehouse
	if doctype in ("Material Request", "Stock Entry"):
		default_warehouse = project.consumables_warehouse or project.default_warehouse

	if default_warehouse:
		out.set_warehouse = default_warehouse

		if purpose == "Material Issue":
			out.from_warehouse = default_warehouse
		elif purpose == "Material Receipt":
			out.to_warehouse = default_warehouse

	frappe.utils.call_hook_method("get_project_details", project, out, doctype)

	return out


@frappe.whitelist()
def make_against_project(project_name, dt):
	project = frappe.get_doc("Project", project_name)
	doc = frappe.new_doc(dt)

	if doc.meta.has_field('company'):
		doc.company = project.company
	if doc.meta.has_field('branch'):
		doc.branch = project.branch
	if doc.meta.has_field('project'):
		doc.project = project_name

	# Set customer
	if project.customer:
		if doc.meta.has_field('customer'):
			doc.customer = project.customer
			doc.update(get_fetch_values(doc.doctype, 'customer', project.customer))
		elif dt == 'Quotation':
			doc.quotation_to = 'Customer'
			doc.party_name = project.customer
			doc.update(get_fetch_values(doc.doctype, 'party_name', project.customer))

	if project.applies_to_item:
		if doc.meta.has_field('item_code'):
			doc.item_code = project.applies_to_item
			doc.update(get_fetch_values(doc.doctype, 'item_code', project.applies_to_item))

			if doc.meta.has_field('serial_no'):
				doc.serial_no = project.serial_no
				doc.update(get_fetch_values(doc.doctype, 'serial_no', project.serial_no))
		else:
			child = doc.append("purposes" if dt == "Maintenance Visit" else "items", {
				"item_code": project.applies_to_item,
				"serial_no": project.serial_no
			})
			child.update(get_fetch_values(child.doctype, 'item_code', project.applies_to_item))
			if child.meta.has_field('serial_no'):
				child.update(get_fetch_values(child.doctype, 'serial_no', project.serial_no))

	doc.run_method("postprocess_after_mapping")

	project.validate_for_transaction(doc)

	return doc


@frappe.whitelist()
def make_sales_invoice(project_name, target_doc=None, depreciation_type=None, claim_billing=None):
	def map_delivery_notes(target, only_items=False, skip_postprocess=False):
		from erpnext.controllers.queries import _get_delivery_notes_to_be_billed
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as invoice_from_delivery_note

		delivery_note_filters = get_filters()
		delivery_note_filters['is_return'] = 0
		delivery_notes = _get_delivery_notes_to_be_billed(filters=delivery_note_filters)

		for d in delivery_notes:
			target = invoice_from_delivery_note(d.name, target_doc=target, only_items=only_items,
				skip_postprocess=skip_postprocess)

		return target

	def map_sales_orders(target, only_items=False, skip_postprocess=False):
		from erpnext.controllers.queries import _get_sales_orders_to_be_billed
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice as invoice_from_sales_order

		sales_order_filters = get_filters()
		sales_orders = _get_sales_orders_to_be_billed(filters=sales_order_filters)

		for d in sales_orders:
			target = invoice_from_sales_order(d.name, target_doc=target, only_items=only_items,
				skip_postprocess=skip_postprocess)

		return target

	def get_filters():
		filters = {"project": project.name}
		if project.company:
			filters['company'] = project.company

		return filters

	def set_project_details():
		target_doc.company = project.company
		target_doc.project = project.name

		for k, v in project_details.items():
			if target_doc.meta.has_field(k):
				target_doc.set(k, v)

	def set_depreciation_type_and_customer():
		has_depreciation_rate = (
			project.default_depreciation_percentage
			or project.default_underinsurance_percentage
			or project.non_standard_depreciation
			or project.non_standard_underinsurance
		)

		if depreciation_type and has_depreciation_rate:
			target_doc.depreciation_type = depreciation_type
			if depreciation_type == "Depreciation Amount Only":
				target_doc.bill_to = target_doc.customer
			elif depreciation_type == "After Depreciation Amount":
				if not project.bill_to and project.insurance_company:
					target_doc.bill_to = project.insurance_company

	def set_cash_or_credit():
		if depreciation_type == 'After Depreciation Amount':
			target_doc.is_pos = 0
		else:
			target_doc.is_pos = project.cash_billing

	def unset_different_customer_details():
		target_billing_customer = target_doc.bill_to or target_doc.customer
		if target_billing_customer != project.customer:
			target_doc.contact_person = None
			target_doc.customer_address = None

	def remove_taxes():
		target_doc.taxes_and_charges = None
		target_doc.taxes = []

	def set_fetch_values():
		target_doc.update(get_fetch_values(target_doc.doctype, 'insurance_company', target_doc.insurance_company))

	def validate_undelivered_sales_order_stock_items():
		undelivered_sales_orders = []
		has_undelivered_items = False
		for d in target_doc.items:
			if d.is_stock_item and not d.delivery_note and (not claim_billing or d.project == project.name):
				has_undelivered_items = True
				if d.sales_order and d.sales_order not in undelivered_sales_orders:
					undelivered_sales_orders.append(d.sales_order)

		if has_undelivered_items:
			undelivered_sales_orders_txt = [frappe.utils.get_link_to_form("Sales Order", so) for so in undelivered_sales_orders]
			undelivered_sales_orders_txt = ", ".join(undelivered_sales_orders_txt)
			if undelivered_sales_orders_txt:
				undelivered_sales_orders_txt = "<br><br>" + undelivered_sales_orders_txt

			frappe.throw(_("{0} has Sales Orders with undelivered stock items. "
				"If you want to bill undelivered stock items, please confirm billing amount and check "
				"<b>'Allow Billing of Undelivered Materials'</b>{1}")
				.format(frappe.get_desk_link("Project", project.name), undelivered_sales_orders_txt),
				title=_("Undelivered Sales Orders"))

	def set_terms_template():
		if project.invoice_terms_template:
			target_doc.tc_name = project.invoice_terms_template

	def set_advances():
		sales_orders = [d.sales_order for d in target_doc.items if d.get("sales_order")]
		if sales_orders:
			target_doc.set_advances(include_unallocated=False)

	if frappe.flags.args and claim_billing is None:
		claim_billing = frappe.flags.args.claim_billing

	claim_billing = cint(claim_billing)

	project = frappe.get_doc("Project", project_name)
	project_details = get_project_details(project, "Sales Invoice")

	# Make Sales Invoice Target Document
	if target_doc and isinstance(target_doc, str):
		target_doc = json.loads(target_doc)

	target_doc = frappe.get_doc(target_doc) if target_doc else frappe.new_doc("Sales Invoice")

	if not claim_billing:
		set_project_details()

	target_doc = map_delivery_notes(target_doc, only_items=claim_billing, skip_postprocess=claim_billing)
	target_doc = map_sales_orders(target_doc, only_items=claim_billing, skip_postprocess=claim_billing)

	if not claim_billing:
		remove_taxes()
		set_project_details()
		set_depreciation_type_and_customer()
		set_cash_or_credit()
		unset_different_customer_details()
		set_fetch_values()
		set_sales_person_in_target_doc(target_doc, project)
		set_terms_template()
		set_advances()

		target_doc.run_method("set_missing_values")
		set_depreciation_in_invoice_items(target_doc.get('items'), project, force=True)
		target_doc.run_method("postprocess_after_mapping", reset_taxes=True)

	# Check Undelivered Sales Order Stock Items
	if not cint(project.get('allow_billing_undelivered_sales_orders')):
		validate_undelivered_sales_order_stock_items()

	if claim_billing:
		frappe.flags.postprocess_after_mapping = postprocess_claim_billing

	project.validate_for_transaction(target_doc)

	return target_doc


def postprocess_claim_billing(target_doc):
	target_doc.ignore_pricing_rule = 1
	target_doc.run_method("postprocess_after_mapping")


@frappe.whitelist()
def make_delivery_note(project_name):
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	project = frappe.get_doc("Project", project_name)
	project_details = get_project_details(project, "Delivery Note")

	# Create Delivery Note
	target_doc = frappe.new_doc("Delivery Note")
	target_doc.company = project.company
	target_doc.project = project.name

	default_transaction_type = frappe.get_cached_value("Projects Settings", None, "default_sales_transaction_type")
	if default_transaction_type:
		target_doc.transaction_type = default_transaction_type

	# Set Project Details
	for k, v in project_details.items():
		if target_doc.meta.has_field(k):
			target_doc.set(k, v)

	# Get Sales Orders
	sales_order_filters = {
		"docstatus": 1,
		"status": ["not in", ["Closed", "On Hold"]],
		"delivery_status": ["=", "To Deliver"],
		"project": project.name,
		"company": project.company,
		"skip_delivery_note": 0,
	}
	sales_orders = frappe.get_all("Sales Order", filters=sales_order_filters)
	for d in sales_orders:
		target_doc = make_delivery_note(d.name, target_doc=target_doc)

	# Remove Taxes (so they are reloaded)
	target_doc.taxes_and_charges = None
	target_doc.taxes = []

	# Set Project Details
	for k, v in project_details.items():
		if target_doc.meta.has_field(k):
			target_doc.set(k, v)

	set_sales_person_in_target_doc(target_doc, project)

	# Missing Values and Forced Values
	target_doc.run_method("postprocess_after_mapping", reset_taxes=True)

	project.validate_for_transaction(target_doc)

	return target_doc


@frappe.whitelist()
def make_sales_order(project_name, items_type=None):
	from erpnext.projects.doctype.project_template.project_template import add_project_template_items

	project = frappe.get_doc("Project", project_name)
	project_details = get_project_details(project, "Sales Order")

	# Create Sales Order
	target_doc = frappe.new_doc("Sales Order")
	target_doc.company = project.company
	target_doc.project = project.name
	target_doc.delivery_date = project.expected_delivery_date

	sales_order_print_heading = frappe.get_cached_value("Projects Settings", None, "sales_order_print_heading")
	if sales_order_print_heading:
		target_doc.select_print_heading = sales_order_print_heading

	default_transaction_type = frappe.get_cached_value("Projects Settings", None, "default_sales_transaction_type")
	if default_transaction_type:
		target_doc.transaction_type = default_transaction_type

	# Set Project Details
	for k, v in project_details.items():
		if target_doc.meta.has_field(k):
			target_doc.set(k, v)

	# Get Project Template Items
	for d in project.project_templates:
		if not d.get('sales_order'):
			target_doc = add_project_template_items(target_doc, d.project_template, project.applies_to_item,
				check_duplicate=False, project_template_detail=d, items_type=items_type)

	set_sales_person_in_target_doc(target_doc, project)

	# Remove already ordered items
	project_template_ordered_set = get_project_template_ordered_set(project)
	to_remove = []
	for d in target_doc.get('items'):
		is_stock_item = 0
		if d.item_code:
			is_stock_item = cint(frappe.get_cached_value("Item", d.item_code, 'is_stock_item'))

		if d.project_template_detail and (d.project_template_detail, is_stock_item) in project_template_ordered_set:
			to_remove.append(d)

	for d in to_remove:
		target_doc.remove(d)
	for i, d in enumerate(target_doc.items):
		d.idx = i + 1

	# Missing Values and Forced Values
	target_doc.run_method("postprocess_after_mapping", reset_taxes=True)

	project.validate_for_transaction(target_doc)

	return target_doc


@frappe.whitelist()
def make_material_request(project_name):
	from erpnext.projects.doctype.project_template.project_template import add_project_template_items

	project = frappe.get_doc("Project", project_name)
	project_details = get_project_details(project, "Material Request")

	# Create Material Request
	target_doc = frappe.new_doc("Material Request")
	target_doc.material_request_type = "Material Issue"
	target_doc.company = project.company
	target_doc.project = project.name
	target_doc.schedule_date = project.expected_delivery_date

	# Set Project Details
	for k, v in project_details.items():
		if target_doc.meta.has_field(k):
			target_doc.set(k, v)

	# Get Project Template Items
	for d in project.project_templates:
		if not d.get('sales_order'):
			target_doc = add_project_template_items(target_doc, d.project_template, project.applies_to_item,
				check_duplicate=False, project_template_detail=d, items_type="stock")

	# Remove already ordered items
	project_template_requested_set = get_project_template_requested_set(project)
	to_remove = []
	for d in target_doc.get('items'):
		if d.project_template_detail and d.project_template_detail in project_template_requested_set:
			to_remove.append(d)

	for d in to_remove:
		target_doc.remove(d)
	for i, d in enumerate(target_doc.items):
		d.idx = i + 1

	# Missing Values and Forced Values
	target_doc.run_method("postprocess_after_mapping")

	project.validate_for_transaction(target_doc)

	return target_doc


@frappe.whitelist()
def make_stock_entry(project_name, purpose):
	from erpnext.stock.doctype.material_request.material_request import make_stock_entry

	if not purpose:
		frappe.throw(_("Purpose not provided"))
	if purpose not in ("Material Issue", "Material Receipt"):
		frappe.throw(_("Invalid Purpose {0}").format(purpose))

	project = frappe.get_doc("Project", project_name)
	project_details = get_project_details(project, "Stock Entry", purpose=purpose)

	# Create Stock Entry
	target_doc = frappe.new_doc("Stock Entry")
	target_doc.company = project.company
	target_doc.project = project.name
	target_doc.purpose = purpose

	# Set Project Details
	for k, v in project_details.items():
		if target_doc.meta.has_field(k):
			target_doc.set(k, v)

	# Map Material Requests
	if purpose == "Material Issue":
		material_request_filters = {
			"docstatus": 1,
			"material_request_type": "Material Issue",
			"status": ["!=", "Stopped"],
			"order_status": "To Order",
			"project": project.name,
			"company": project.company,
		}
		material_requests = frappe.get_all("Material Request", filters=material_request_filters)
		for d in material_requests:
			target_doc = make_stock_entry(d.name, target_doc=target_doc)
	elif purpose == "Material Receipt":
		returnable = get_returnable_consumables(project.name)
		for d in returnable:
			row = target_doc.append("items", frappe.new_doc("Stock Entry Detail"))
			row.update(d)
			row.qty = 0

	# Set Project Details
	for k, v in project_details.items():
		if target_doc.meta.has_field(k):
			target_doc.set(k, v)

	# Missing Values and Forced Values
	target_doc.set_stock_entry_type()
	target_doc.run_method("postprocess_after_mapping")

	project.validate_for_transaction(target_doc)

	return target_doc


def get_returnable_consumables(project):
	material_issue_data = frappe.db.sql("""
		select i.item_code, sum(i.qty) as qty, i.uom
		from `tabStock Entry Detail` i
		inner join `tabStock Entry` ste on ste.name = i.parent
		where ste.project = %s and ste.docstatus = 1 and ste.purpose = 'Material Issue'
		group by i.item_code, i.uom
	""", project, as_dict=1)

	material_receipt_data = frappe.db.sql("""
		select i.item_code, sum(i.qty) as qty, i.uom
		from `tabStock Entry Detail` i
		inner join `tabStock Entry` ste on ste.name = i.parent
		where ste.project = %s and ste.docstatus = 1 and ste.purpose = 'Material Receipt'
		group by i.item_code, i.uom
	""", project, as_dict=1)

	material_map = {}
	for d in material_issue_data:
		key = (d.item_code, d.uom)
		row = material_map.setdefault(key, frappe._dict({"item_code": d.item_code, "uom": d.uom, "qty": 0}))
		row.qty += d.qty

	for d in material_receipt_data:
		key = (d.item_code, d.uom)
		if key not in material_map:
			continue

		row = material_map[key]
		row.qty -= d.qty

	out = list(material_map.values())
	for d in out:
		d.qty = flt(d.qty, frappe.get_precision("Stock Entry Detail", "qty"))

	out = [d for d in out if d.qty > 0]
	return out


def set_sales_person_in_target_doc(target_doc, project):
	if project.service_advisor:
		target_doc.sales_team = []
		target_doc.append("sales_team", {
			"sales_person": project.service_advisor,
			"allocated_percentage": 100
		})


def get_project_template_ordered_set(project):
	project_template_ordered_set = []

	project_template_details = [d.name for d in project.project_templates]
	if project_template_details:
		project_template_ordered_set = frappe.db.sql("""
			select distinct item.project_template_detail, item.is_stock_item
			from `tabSales Order Item` item
			inner join `tabSales Order` so on so.name = item.parent
			where so.docstatus = 1 and so.project = %s and item.project_template_detail in %s
		""", (project.name, project_template_details))

	return project_template_ordered_set


def get_project_template_requested_set(project):
	project_template_requested_set = []

	project_template_details = [d.name for d in project.project_templates]
	if project_template_details:
		project_template_requested_set = frappe.db.sql_list("""
			select distinct item.project_template_detail
			from `tabMaterial Request Item` item
			inner join `tabMaterial Request` mreq on mreq.name = item.parent
			where mreq.docstatus = 1 and mreq.project = %s and item.project_template_detail in %s
		""", (project.name, project_template_details))

	return project_template_requested_set


def set_depreciation_in_invoice_items(items_list, project, force=False):
	non_standard_depreciation_items = {}
	for d in project.non_standard_depreciation:
		if d.depreciation_item_code:
			non_standard_depreciation_items[d.depreciation_item_code] = flt(d.depreciation_percentage)

	non_standard_underinsurance_items = {}
	for d in project.non_standard_underinsurance:
		if d.underinsurance_item_code:
			non_standard_underinsurance_items[d.underinsurance_item_code] = flt(d.underinsurance_percentage)

	materials_item_groups = project.get_item_groups_subtree(project.materials_item_group)

	for d in items_list:
		is_material = d.is_stock_item or d.item_group in materials_item_groups
		if is_material or d.item_code in non_standard_depreciation_items:
			if force or not flt(d.depreciation_percentage):
				if d.item_code in non_standard_depreciation_items:
					d.depreciation_percentage = non_standard_depreciation_items[d.item_code]
				else:
					d.depreciation_percentage = flt(project.default_depreciation_percentage)
		else:
			d.depreciation_percentage = 0

		if force or not flt(d.underinsurance_percentage):
			if d.item_code in non_standard_underinsurance_items:
				d.underinsurance_percentage = non_standard_underinsurance_items[d.item_code]
			else:
				d.underinsurance_percentage = flt(project.default_underinsurance_percentage)


@frappe.whitelist()
def set_warranty_claim_denied(projects, denied, reason=None):
	if isinstance(projects, str):
		projects = json.loads(projects)

	denied = cint(denied)

	for name in projects:
		doc = frappe.get_doc("Project", name)
		doc.warranty_claim_denied = denied
		doc.warranty_claim_denied_reason = reason
		doc.save()
