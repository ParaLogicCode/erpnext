# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, get_datetime, add_to_date, time_diff_in_hours
import json


class OverlapError(frappe.ValidationError): pass


class Timesheet(Document):
	def validate(self):
		self.set_missing_values()
		self.validate_dates()
		self.validate_time_logs()
		self.calculate_totals()
		self.calculate_percentage_billed()
		self.set_dates()
		self.set_status()

	def on_submit(self):
		self.validate_mandatory_fields()
		self.update_task_and_project()

	def on_cancel(self):
		self.set_status()
		self.update_task_and_project()

	def on_update(self):
		if self.docstatus == 0:
			self.update_task_and_project()

	def after_delete(self):
		self.update_task_and_project()

	def set_missing_values(self):
		for d in self.time_logs:
			if d.task and not d.project:
				d.project = frappe.db.get_value("Task", d.task, "project")

			if d.from_time:
				if d.hours:
					d.to_time = get_datetime(add_to_date(d.from_time, hours=d.hours, as_datetime=True))
				if d.to_time:
					d.hours = time_diff_in_hours(d.to_time, d.from_time)

			rate = get_activity_cost(self.employee, d.activity_type)
			if rate:
				d.billing_rate = flt(d.billing_rate) or flt(rate.get('billing_rate'))
				d.costing_rate = flt(d.costing_rate) or flt(rate.get('costing_rate'))

	def validate_dates(self):
		for d in self.time_logs:
			if d.from_time and d.to_time and get_datetime(d.from_time) > get_datetime(d.to_time):
				frappe.throw(_("Row {0}: Incorrect time range").format(d.idx))

	def validate_time_logs(self):
		if not self.employee or frappe.db.get_single_value("Projects Settings", 'ignore_employee_time_overlap'):
			return

		for d in self.time_logs:
			self.validate_overlap_for_timelog(d)

	def calculate_totals(self):
		self.total_hours = 0
		self.total_costing_amount = 0
		self.total_billable_hours = 0
		self.total_billable_amount = 0
		self.total_billed_hours = 0.0
		self.total_billed_amount = 0.0

		for d in self.time_logs:
			self.round_floats_in(d)
			self.set_hours_and_to_time(d)

			d.costing_amount = flt(d.costing_rate * d.hours)

			self.total_hours += d.hours
			self.total_costing_amount += d.costing_amount

			if d.billable:
				d.billing_hours = flt(d.billing_hours) or flt(d.hours)
				d.billing_amount = flt(d.billing_rate * d.billing_hours)

				self.total_billable_hours += d.billing_hours
				self.total_billable_amount += d.billing_amount
				self.total_billed_hours += flt(d.billing_hours) if d.sales_invoice else 0.0
				self.total_billed_amount += flt(d.billing_amount) if d.sales_invoice else 0.0
			else:
				d.billing_hours = 0.0
				d.billing_rate = 0.0

	def calculate_percentage_billed(self):
		self.per_billed = 0
		if self.total_billed_amount > 0 and self.total_billable_amount > 0:
			self.per_billed = (self.total_billed_amount * 100) / self.total_billable_amount
		elif self.total_billed_hours > 0 and self.total_billable_hours > 0:
			self.per_billed = (self.total_billed_hours * 100) / self.total_billable_hours

	def set_dates(self):
		if self.docstatus < 2 and self.time_logs:
			self.start_date = min(getdate(d.from_time) for d in self.time_logs)
			self.end_date = max(getdate(d.to_time) for d in self.time_logs)

	def set_status(self):
		self.status = {
			"0": "Draft",
			"1": "Submitted",
			"2": "Cancelled"
		}[str(self.docstatus or 0)]

		if self.per_billed == 100:
			self.status = "Billed"

		if self.sales_invoice:
			self.status = "Completed"

	def set_hours_and_to_time(self, row):
		if row.from_time:
			if row.hours:
				row.to_time = get_datetime(add_to_date(row.from_time, hours=row.hours, as_datetime=True))
			if row.to_time:
				row.hours = time_diff_in_hours(row.to_time, row.from_time)

	def validate_mandatory_fields(self):
		for d in self.time_logs:
			if not d.from_time and not d.to_time:
				frappe.throw(_("Row {0}: From Time and To Time is mandatory.").format(d.idx))

			if not d.activity_type and self.employee:
				frappe.throw(_("Row {0}: Activity Type is mandatory.").format(d.idx))

			if flt(d.hours) == 0.0:
				frappe.throw(_("Row {0}: Hours value must be greater than zero.").format(d.idx))

	def update_task_and_project(self):
		tasks, projects = set(), set()

		for d in self.time_logs:
			if d.task:
				tasks.add(d.task)
			if d.project:
				projects.add(d.project)

		if not self.flags.do_not_update_task:
			for task in tasks:
				doc = frappe.get_doc("Task", task)
				doc.set_time_and_costing(update=True)

		for project in projects:
			doc = frappe.get_doc("Project", project)
			doc.set_timesheet_values(update=True)
			doc.set_gross_margin(update=True)

	def validate_overlap_for_timelog(self, row):
		if not row.from_time and not row.to_time:
			return

		if row.from_time and row.to_time:
			overlap_condition = "(%(from_time)s < tsd.to_time OR tsd.to_time is null) AND %(to_time)s > tsd.from_time"
		elif row.from_time:
			overlap_condition = "%(from_time)s >= tsd.from_time AND (%(from_time)s < tsd.to_time or tsd.to_time is null)"
		else:
			return

		existing = frappe.db.sql(f"""
			SELECT tsd.parent, tsd.idx
			FROM `tabTimesheet Detail` tsd
			LEFT JOIN `tabTimesheet` ts On ts.name = tsd.parent
			WHERE ts.docstatus < 2
				AND tsd.name != %(row_name)s
				AND ts.employee = %(employee)s
				AND {overlap_condition}
		""", {
			"employee": self.employee,
			"row_name": row.name,
			"from_time": get_datetime(row.from_time),
			"to_time": get_datetime(row.to_time),
		}, as_dict=1)

		if existing:
			frappe.throw(_("Row {0}: From Time and To Time of {1} is overlapping with Row {2} of {3}")
				.format(row.idx, self.name, existing[0].idx, existing[0].parent), OverlapError)


@frappe.whitelist()
def make_sales_invoice(source_name, item_code=None, customer=None):
	ts_doc = frappe.get_doc('Timesheet', source_name)

	if not ts_doc.total_billable_hours:
		frappe.throw(_("Invoice can't be made for zero billing hour"))

	if ts_doc.total_billable_hours == ts_doc.total_billed_hours:
		frappe.throw(_("Invoice already created for all billing hours"))

	hours = flt(ts_doc.total_billable_hours) - flt(ts_doc.total_billed_hours)
	billing_amount = flt(ts_doc.total_billable_amount) - flt(ts_doc.total_billed_amount)
	billing_rate = billing_amount / hours

	target = frappe.new_doc("Sales Invoice")
	target.company = ts_doc.company
	if customer:
		target.customer = customer

	if item_code:
		target.append('items', {
			'item_code': item_code,
			'qty': hours,
			'rate': billing_rate
		})

	target.append('timesheets', {
		'time_sheet': ts_doc.name,
		'billing_hours': hours,
		'billing_amount': billing_amount
	})

	target.run_method("calculate_billing_amount_for_timesheet")
	target.run_method("set_missing_values")

	return target


@frappe.whitelist()
def make_salary_slip(source_name, target_doc=None):
	doc = frappe.get_doc('Timesheet', source_name)

	if not target_doc:
		target_doc = frappe.new_doc("Salary Slip")

	target_doc.employee = doc.employee
	target_doc.employee_name = doc.employee_name
	target_doc.salary_slip_based_on_timesheet = 1
	target_doc.start_date = doc.start_date
	target_doc.end_date = doc.end_date
	target_doc.posting_date = doc.modified
	target_doc.total_working_hours = doc.total_hours
	target_doc.append('timesheets', {
		'time_sheet': doc.name,
		'working_hours': doc.total_hours
	})

	target_doc.run_method("get_emp_and_leave_details")
	return target_doc


@frappe.whitelist()
def get_activity_cost(employee=None, activity_type=None):
	fields = ["costing_rate", "billing_rate"]
	filters = {"employee": employee, "activity_type": activity_type}
	rate = frappe.db.get_values("Activity Cost", filters, fields, as_dict=True)
	if not rate:
		filters.pop('employee')
		rate = frappe.db.get_values("Activity Type", filters, fields,as_dict=True)

	return rate[0] if rate else {}


@frappe.whitelist()
def get_projectwise_timesheet_data(project=None, timesheet=None):
	condition = ""
	if project:
		condition += "AND tsd.project = %(project)s "
	if timesheet:
		condition += "AND tsd.parent = %(timesheet)s "

	return frappe.db.sql("""
		SELECT tsd.name, tsd.parent as timesheet, tsd.activity_type,
			tsd.from_time, tsd.to_time, tsd.billing_hours, tsd.billing_amount
		FROM `tabTimesheet Detail` tsd
		INNER JOIN `tabTimesheet` ts ON ts.name = tsd.parent
		WHERE ts.docstatus = 1 AND tsd.billable = 1
			AND tsd.sales_invoice is NULL {0}
		ORDER BY tsd.from_time ASC
	""".format(condition), {"project": project, "timesheet": timesheet}, as_dict=1)


@frappe.whitelist()
def get_timesheet_data(name, project):
	data = None
	if project and project!='':
		data = get_projectwise_timesheet_data(project, name)
	else:
		data = frappe.get_all('Timesheet',
			fields = ["(total_billable_amount - total_billed_amount) AS billing_amt", "total_billable_hours AS billing_hours"], filters = {'name': name})
	return {
		'billing_hours': data[0].billing_hours if data else None,
		'billing_amount': data[0].billing_amt if data else None,
		'timesheet_detail': data[0].name if data and project and project!= '' else None
	}


@frappe.whitelist()
def get_events(start, end, filters=None):
	from frappe.desk.calendar import get_event_conditions
	from erpnext.controllers.queries import get_match_cond

	filters = json.loads(filters)
	conditions = get_event_conditions("Timesheet", filters)
	match_cond = get_match_cond('Timesheet')

	return frappe.db.sql("""
		SELECT tsd.name, tsd.docstatus AS status, tsd.parent,
			tsd.activity_type, tsd.project, tsd.hours,
			tsd.from_time AS start_date, tsd.to_time AS end_date,
			CONCAT(tsd.parent, ' (', ROUND(tsd.hours, 2), ' hrs)') AS title
		FROM `tabTimesheet Detail` tsd
		INNER JOIN `tabTimesheet` ts ON ts.name = tsd.parent
		WHERE ts.docstatus < 2
			AND (tsd.from_time <= %(end)s AND tsd.to_time >= %(start)s)
			{conditions} {match_cond}
		""".format(conditions=conditions, match_cond=match_cond),
		{"start": start, "end": end}, as_dict=True, update={"allDay": 0})
