# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _, throw
from frappe.desk.form.assign_to import clear, close_all_assignments
from frappe.model.mapper import get_mapped_doc
from frappe.utils import (
	add_days, flt, cstr, cint, date_diff, get_link_to_form, get_url_to_form, getdate, today, get_datetime, now_datetime
)
from frappe.utils.nestedset import NestedSet
from erpnext.stock.get_item_details import get_applies_to_details, get_force_applies_to_fields
from erpnext.hr.doctype.employee.employee import get_employee_from_user
import json


class CircularReferenceError(frappe.ValidationError): pass
class EndDateCannotBeGreaterThanProjectEndDateError(frappe.ValidationError): pass


task_status_color_map = {
	"Open": "orange",
	"Working": "purple",
	"On Hold": "red",
	"Completed": "green",
	"Cancelled": "light-gray"
}


class Task(NestedSet):
	nsm_parent_field = 'parent_task'

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		self.force_applies_to_fields = get_force_applies_to_fields(self.doctype)

	def get_feed(self):
		return '{0}: {1}'.format(_(self.status), self.subject)

	def onload(self):
		self.set_onload("action_conditions", get_task_action_conditions(self))

		timelogs = self.get_timelogs()
		self.set_onload("timelogs", timelogs)
		self.set_timelogs_html_onload(timelogs)

	def validate(self):
		self.set_previous_values()
		self.set_missing_values()
		self.validate_before_status()
		self.set_status()
		self.validate_after_status()

	def validate_before_status(self):
		self.set_depends_on()
		self.validate_dates()

	def set_status(self):
		timelogs = self.get_timelogs(cache=False)
		running_timelogs = [tl for tl in timelogs if not tl.to_time]

		if running_timelogs:
			self.status = "Working"

	def validate_after_status(self):
		self.validate_cant_change()
		self.validate_project_ready_to_close()
		self.set_time_and_costing()
		self.validate_progress()
		self.validate_status_depedency()
		self.set_completion_values()
		self.set_is_overdue()

	def before_insert(self):
		self.validate_project_ready_to_close_before_insert()

	def on_update(self):
		self.update_nsm_model()
		self.check_recursion()
		self.reschedule_dependent_tasks()
		self.update_project()
		self.unassign_todo()
		self.populate_depends_on()

	def on_trash(self):
		if check_if_child_exists(self.name):
			throw(_("Child Task exists for this Task. You can not delete this Task."))

		self.update_nsm_model()

	def after_delete(self):
		self.update_project()

	def set_previous_values(self):
		if self.is_new():
			self._previous_values = frappe._dict({
				"status": "Open",
			})
		else:
			self._previous_values = frappe.db.get_value(self.doctype, self.name, fieldname=[
				"status", "assigned_to", "project", "issue",
			], as_dict=1)

	def get_previous_value(self, fieldname):
		return self._previous_values.get(fieldname)

	def value_changed(self, fieldname):
		return cstr(self.get(fieldname)) != cstr(self._previous_values.get(fieldname))

	def set_missing_values(self):
		self.set_applies_to_details()

		if self.assigned_to:
			self.assigned_to_name = frappe.get_cached_value("Employee", self.assigned_to, "employee_name")
		else:
			self.assigned_to_name = None

	def set_applies_to_details(self):
		args = self.as_dict()
		applies_to_details = get_applies_to_details(args, for_validate=True)

		for k, v in applies_to_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in self.force_applies_to_fields:
				self.set(k, v)

	def validate_cant_change(self):
		if self.value_changed("project") and not self.is_new():
			if self.service_template_detail:
				frappe.throw(_("Cannot change {0} for Service Template task").format(
					_("Project")
				))

			if self.status != "Open":
				frappe.throw(_("Cannot change {0} when status in {1}").format(
					_("Project"), frappe.bold(self.status)
				))

		if self.value_changed("issue") and not self.is_new():
			if self.status != "Open":
				frappe.throw(_("Cannot change Issue when status in {0}").format(frappe.bold(self.status)))

	def validate_project_ready_to_close(self):
		if not self.project:
			return

		if self.status not in ("Completed", "Cancelled") and self.value_changed("status"):
			ready_to_close = frappe.db.get_value("Project", self.project, "ready_to_close", cache=1)
			if ready_to_close:
				frappe.throw(_("Cannot change status to {0} because {1} is Ready to Close").format(
					frappe.bold(self.status), get_link_from_name("Project", self.project)
				))

	def validate_project_ready_to_close_before_insert(self):
		if not self.project:
			return

		ready_to_close = frappe.db.get_value("Project", self.project, "ready_to_close", cache=1)
		if ready_to_close:
			frappe.throw(_("Cannot create Task against {1} because it is Ready to Close").format(
				frappe.bold(self.status), get_link_from_name("Project", self.project)
			))

	def validate_dates(self):
		if self.exp_start_date and self.exp_end_date and getdate(self.exp_start_date) > getdate(self.exp_end_date):
			frappe.throw(_("{0} can not be greater than {1}").format(
				frappe.bold("Expected Start Date"),
				frappe.bold("Expected End Date")
			))

	def validate_progress(self):
		if flt(self.progress) > 100:
			frappe.throw(_("Progress % for a task cannot be more than 100."))
		if flt(self.progress) < 0:
			frappe.throw(_("Progress % cannot be negative"))

		if self.status == 'Completed':
			self.progress = 100

	def validate_status_depedency(self):
		if self.value_changed("status") and self.status == "Completed":
			for d in self.depends_on:
				if frappe.db.get_value("Task", d.task, "status") not in ("Completed", "Cancelled"):
					frappe.throw(_("Cannot complete task {0} as its dependant {1} is not completed / cancelled.")
						.format(frappe.bold(self.name), frappe.get_desk_link("Task", d.task)))

	def set_completion_values(self):
		if self.value_changed("status") and self.status == "Completed":
			if not self.finish_date:
				self.finish_date = today()

	def set_depends_on(self):
		depends_on_tasks = []
		for d in self.depends_on:
			if d.task and d.task not in depends_on_tasks:
				depends_on_tasks.append(d.task)

		self.depends_on_tasks = ", ".join(depends_on_tasks)

	def populate_depends_on(self):
		if self.parent_task:
			parent = frappe.get_doc('Task', self.parent_task)
			if not self.name in [row.task for row in parent.depends_on]:
				parent.append("depends_on", {
					"doctype": "Task Depends On",
					"task": self.name,
					"subject": self.subject
				})
				parent.save()

	def update_nsm_model(self):
		frappe.utils.nestedset.update_nsm(self)

	def unassign_todo(self):
		if self.status == "Completed":
			close_all_assignments(self.doctype, self.name)
		if self.status == "Cancelled":
			clear(self.doctype, self.name)

	def update_total_expense_claim(self):
		self.total_expense_claim = frappe.db.sql("""
			select sum(sanctioned_amount)
			from `tabExpense Claim Detail`
			where project = %s and task = %s and docstatus=1
		""", (self.project, self.name))[0][0]

	def set_time_and_costing(self, update=False, update_modified=True):
		tl = frappe.db.sql("""
			select
				min(from_time) as from_time,
				max(to_time) as to_time,
				sum(billing_amount) as total_billing_amount,
				sum(costing_amount) as total_costing_amount,
				sum(hours) as time
			from `tabTimesheet Detail`
			where task = %s and docstatus < 2
		""", self.name, as_dict=1)
		tl = tl[0] if tl else frappe._dict()

		self.total_costing_amount = flt(tl.total_costing_amount)
		self.total_billing_amount = flt(tl.total_billing_amount)
		self.actual_time = flt(tl.time)
		self.act_start_date = tl.from_time
		self.act_end_date = tl.to_time if self.status in ("Completed", "Cancelled") else None

		if update:
			self.db_set({
				"total_costing_amount": self.total_costing_amount,
				"total_billing_amount": self.total_billing_amount,
				"actual_time": self.actual_time,
				"act_start_date": self.act_start_date,
				"act_end_date": self.act_end_date,
			}, update_modified=update_modified)

	def update_project(self):
		if self.project and not self.flags.from_project:
			doc = frappe.get_doc("Project", self.project)
			doc.set_tasks_status(update=True)
			doc.set_percent_complete(update=True)
			doc.set_status(update=True, from_doctype=self.doctype, action=self.get("_action"))
			doc.notify_update()

	def check_recursion(self):
		if self.flags.ignore_recursion_check:
			return

		check_list = [['task', 'parent'], ['parent', 'task']]
		for d in check_list:
			task_list, count = [self.name], 0
			while len(task_list) > count:
				tasks = frappe.db.sql("""
					select {0}
					from `tabTask Depends On`
					where {1} = %s
				""".format(d[0], d[1]), cstr(task_list[count]))
				count = count + 1
				for b in tasks:
					if b[0] == self.name:
						frappe.throw(_("Circular Reference Error"), CircularReferenceError)
					if b[0]:
						task_list.append(b[0])

				if count == 15:
					break

	def reschedule_dependent_tasks(self):
		end_date = self.exp_end_date or self.act_end_date
		if end_date:
			for task_name in frappe.db.sql("""
				select name from `tabTask` as parent
				where parent.project = %(project)s
					and parent.name in (
						select parent from `tabTask Depends On` as child
						where child.task = %(task)s and child.project = %(project)s)
			""", {'project': self.project, 'task':self.name }, as_dict=1):
				task = frappe.get_doc("Task", task_name.name)
				if task.exp_start_date and task.exp_end_date and task.exp_start_date < getdate(end_date) and task.status == "Open":
					task_duration = date_diff(task.exp_end_date, task.exp_start_date)
					task.exp_start_date = add_days(end_date, 1)
					task.exp_end_date = add_days(task.exp_start_date, task_duration)
					task.flags.ignore_recursion_check = True
					task.save()

	def set_is_overdue(self, update=False, update_modified=False):
		self.is_overdue = 0

		if self.status not in ["Completed", "Cancelled"]:
			if self.exp_end_date and getdate(self.exp_end_date) < getdate():
				self.is_overdue = 1

		if update:
			self.db_set('is_overdue', self.is_overdue, update_modified=update_modified)

	def check_clocking_permission(self):
		self.check_permission("read")

		can_clock_task = has_task_clocking_permission(self.assigned_to)
		if not can_clock_task:
			frappe.throw(_("Insufficient Permission for Task Clocking"), exc=frappe.PermissionError)

	def set_timelogs_html_onload(self, timelogs):
		timelogs_html = frappe.render_template("erpnext/projects/doctype/task/task_timelogs_table.html", {
			"doc": self,
			"data": timelogs,
			"totals": get_timelog_totals(timelogs),
		})

		self.set_onload('timelogs_html', timelogs_html)

	def get_timelogs(self, cache=False):
		if self.is_new():
			return []

		def generator():
			self._timelogs = frappe.db.sql("""
				SELECT
					ts.name as timesheet, tsd.name as time_log_row,
					ts.employee, ts.employee_name, 
					tsd.from_time, tsd.to_time,
					tsd.activity_type, tsd.hours
				FROM `tabTimesheet Detail` tsd
				INNER JOIN tabTimesheet ts ON ts.name = tsd.parent
				WHERE tsd.task = %s and ts.docstatus < 2
				ORDER BY from_time
			""", self.name, as_dict=1)

			return self._timelogs

		if cache and self.get("_timelogs") is not None:
			timelogs = self.get("_timelogs") or []
		else:
			timelogs = generator() or []

		set_hrs_for_running_timelogs(timelogs)

		return timelogs


@frappe.whitelist()
def check_if_child_exists(name):
	child_tasks = frappe.get_all("Task", filters={"parent_task": name})
	child_tasks = [get_link_to_form("Task", task.name) for task in child_tasks]
	return child_tasks


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_project(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_match_cond
	return frappe.db.sql(""" select name from `tabProject`
			where %(key)s like %(txt)s
				%(mcond)s
			order by name
			limit %(start)s, %(page_len)s""" % {
				'key': searchfield,
				'txt': frappe.db.escape('%' + txt + '%'),
				'mcond':get_match_cond(doctype),
				'start': start,
				'page_len': page_len
			})


def set_tasks_as_overdue():
	tasks = frappe.get_all("Task", filters={
		"status": ["not in", ["Cancelled", "Completed"]],
		"exp_end_date": ["<", today()],
	})

	for task in tasks:
		doc = frappe.get_doc("Task", task.name)
		doc.set_is_overdue(update=True)


@frappe.whitelist()
def make_timesheet(source_name, target_doc=None, ignore_permissions=False):
	def set_missing_values(source, target):
		target.append("time_logs", {
			"hours": source.actual_time,
			"completed": source.status == "Completed",
			"project": source.project,
			"task": source.name
		})

	doclist = get_mapped_doc("Task", source_name, {
			"Task": {
				"doctype": "Timesheet"
			}
		}, target_doc, postprocess=set_missing_values, ignore_permissions=ignore_permissions)

	return doclist


@frappe.whitelist()
def get_children(doctype, parent, task=None, project=None, status=None, is_root=False):

	filters = [['docstatus', '<', '2']]

	if project:
		filters.append(['project', '=', project])

	if task:
		filters.append(['parent_task', '=', task])
	elif parent and not is_root:
		# via expand child
		filters.append(['parent_task', '=', parent])
	else:
		filters.append(['ifnull(`parent_task`, "")', '=', ''])

	if status:
		if status == "Open":
			filters.append(['status', 'not in', ['Completed', 'Cancelled']])
		elif status == "Completed":
			filters.append(['status', '=', 'Completed'])

	tasks = frappe.get_list(doctype, fields=[
		'name as value',
		'subject as title',
		'is_group as expandable',
		'project',
		'issue'
	], filters=filters, order_by='name')

	# return tasks
	return tasks


@frappe.whitelist()
def add_node():
	from frappe.desk.treeview import make_tree_args
	args = frappe.form_dict
	args.update({
		"name_field": "subject"
	})
	args = make_tree_args(**args)

	if args.parent_task == 'All Tasks' or args.parent_task == args.project:
		args.parent_task = None

	frappe.get_doc(args).insert()


@frappe.whitelist()
def add_multiple_tasks(data, parent):
	data = json.loads(data)
	new_doc = {'doctype': 'Task', 'parent_task': parent if parent!="All Tasks" else ""}
	new_doc['project'] = frappe.db.get_value('Task', {"name": parent}, 'project') or ""

	for d in data:
		if not d.get("subject"): continue
		new_doc['subject'] = d.get("subject")
		new_task = frappe.get_doc(new_doc)
		new_task.insert()


@frappe.whitelist()
def create_service_template_tasks(project):
	frappe.has_permission("Task", "create", throw=True)

	project_doc = frappe.get_doc("Project", project)

	if not project_doc.service_templates:
		frappe.throw(_("No Service Template set in {0}".format(get_link_from_name("Project", project))))

	tasks_created = []
	tasks_exists = False
	for service_template_row in project_doc.service_templates:
		filters = {
			"project": project_doc.name,
			"service_template": service_template_row.service_template,
			"service_template_detail": service_template_row.name
		}
		if frappe.db.exists("Task", filters):
			tasks_exists = True
			continue

		template_doc = frappe.get_cached_doc("Service Template", service_template_row.service_template)
		for template_task_row in template_doc.tasks:
			task_doc = frappe.new_doc("Task")
			task_doc.project = project_doc.name
			task_doc.subject = template_task_row.subject
			task_doc.description = template_task_row.description
			task_doc.task_type = template_task_row.task_type
			task_doc.expected_time = template_task_row.expected_time
			task_doc.service_template = service_template_row.service_template
			task_doc.service_template_detail = service_template_row.name

			if template_task_row.use_template_name:
				task_doc.subject = service_template_row.service_template_name
			if template_task_row.use_template_description:
				task_doc.description = service_template_row.description

			if template_task_row.determine_time:
				determined_time = determine_time_from_service_item(project_doc, template_doc,
					service_template_detail=service_template_row)
				if determined_time:
					task_doc.expected_time = determined_time

			task_doc.save()
			tasks_created.append(task_doc)

	if tasks_created:
		frappe.msgprint(_("{0} Service Template tasks created against {1}<br><br><ul>{2}</ul>").format(
			len(tasks_created),
			get_link_from_name("Project", project_doc.name),
			"".join([f"<li>{get_link(d)}</li>" for d in tasks_created])
		), indicator="green")
	elif tasks_exists:
		frappe.msgprint(_("Service Template tasks against {0} already created").format(
			get_link_from_name("Project", project_doc.name)
		))
	else:
		frappe.msgprint(_("There are no Service Templates with tasks in {0}").format(
			get_link_from_name("Project", project_doc.name)
		))


def determine_time_from_service_item(project_doc, template_doc, service_template_detail=None):
	from erpnext.projects.doctype.service_template.service_template import get_service_template_items
	from erpnext.stock.doctype.item.item import convert_item_uom_for

	service_items = get_service_template_items(
		template_doc.name,
		items_table="sales_items",
		applies_to_item=project_doc.applies_to_item,
		applies_to_customer=project_doc.customer,
		items_type="service",
	)

	service_item_codes = [pt.applicable_item_code for pt in service_items if pt.applicable_item_code]

	# Look in Sales Order first
	sales_order_items = []
	if service_template_detail:
		sales_order_items = frappe.db.sql("""
			select item_code, qty, uom
			from `tabSales Order Item` i
			inner join `tabSales Order` so on so.name = i.parent
			where so.docstatus = 1
				and so.project = %(project)s
				and i.service_template = %(service_template)s
				and i.service_template_detail = %(service_template_detail)s
				and i.item_code in %(service_item_codes)s
			order by so.transaction_date, i.idx
		""", {
			"project": project_doc.name,
			"service_template": template_doc.name,
			"service_template_detail": service_template_detail.name,
			"service_item_codes": service_item_codes,
		}, as_dict=1)

	for soi in sales_order_items:
		if not soi.item_code:
			continue

		time = convert_item_uom_for(
			soi.qty,
			item_code=soi.item_code,
			from_uom=soi.uom,
			to_uom="Hour",
			null_if_not_convertible=True,
		)

		if time is not None:
			return flt(time, frappe.get_precision("Task", "expected_time"))

	# Then look in Service Template items table
	for pt in service_items:
		if not pt.applicable_item_code:
			continue

		time = convert_item_uom_for(
			pt.applicable_qty,
			item_code=pt.applicable_item_code,
			from_uom=pt.applicable_uom,
			to_uom="Hour",
			null_if_not_convertible=True,
		)

		if time is not None:
			return flt(time, frappe.get_precision("Task", "expected_time"))

	return 0


@frappe.whitelist()
def create_task(subject, project=None, task_type=None, expected_time=None):
	frappe.has_permission("Task", "create", throw=True)

	if not subject:
		frappe.throw(_("Subject is mandatory"))

	task_doc = get_new_task(
		subject=subject,
		project=project,
		task_type=task_type,
		expected_time=expected_time,
	)

	task_doc.save()

	frappe.msgprint(_("{0} created").format(
		get_link(task_doc)
	), indicator="green")


def get_new_task(subject, project=None, task_type=None, expected_time=None):
	task_doc = frappe.new_doc("Task")
	task_doc.project = project
	task_doc.subject = subject
	task_doc.task_type = task_type
	task_doc.expected_time = flt(expected_time)

	return task_doc


@frappe.whitelist()
def start_task(task):
	task_doc = frappe.get_doc("Task", task)
	task_doc.check_clocking_permission()

	if not task_doc.assigned_to:
		frappe.throw(_("{0} is not set for {0}").format(
			task.meta.get_label("assigned_to"),
			get_link(task_doc)
		))

	if task_doc.status != "Open":
		frappe.throw(_("Cannot start {0} because its status is {1}").format(
			get_link(task_doc),
			frappe.bold(task_doc.status)
		))

	check_assigned_to_availability(task_doc.assigned_to, throw=True)

	add_timesheet_log(task_doc.name, task_doc.assigned_to, project=task_doc.project)

	task_doc.status = "Working"
	task_doc.save(ignore_permissions=True)

	frappe.msgprint(_("{0} started").format(
		get_link(task_doc)
	), alert=True, indicator="green")


@frappe.whitelist()
def pause_task(task):
	task_doc = frappe.get_doc("Task", task)
	task_doc.check_clocking_permission()

	if task_doc.status != "Working":
		frappe.throw(_("Cannot pause {0} because its status is {1}").format(
			get_link(task_doc),
			frappe.bold(task_doc.status)
		))

	stop_timesheet_log(task_doc.name, task_doc.assigned_to, completed=0)

	task_doc.status = "On Hold"
	task_doc.save(ignore_permissions=True)

	frappe.msgprint(_("{0} paused").format(
		get_link(task_doc)
	), alert=True, indicator="green")


@frappe.whitelist()
def resume_task(task):
	task_doc = frappe.get_doc("Task", task)
	task_doc.check_clocking_permission()

	if task_doc.status != "On Hold":
		frappe.throw(_("Cannot resume {0} because its status is {1}").format(
			get_link(task_doc),
			frappe.bold(task_doc.status)
		))

	check_assigned_to_availability(task_doc.assigned_to, throw=True)

	add_timesheet_log(task_doc.name, task_doc.assigned_to, project=task_doc.project)

	task_doc.status = "Working"
	task_doc.save(ignore_permissions=True)

	frappe.msgprint(_("{0} resumed").format(
		get_link(task_doc)
	), alert=True, indicator="green")


@frappe.whitelist()
def complete_task(task):
	task_doc = frappe.get_doc("Task", task)
	task_doc.check_clocking_permission()

	if task_doc.status not in ("Working", "On Hold"):
		frappe.throw(_("Cannot complete {0} because its status is {1}").format(
			get_link(task_doc),
			frappe.bold(task_doc.status)
		))

	stop_timesheet_log(task_doc.name, task_doc.assigned_to, completed=1)

	task_doc.status = "Completed"
	task_doc.save(ignore_permissions=True)

	frappe.msgprint(_("{0} completed").format(
		get_link(task_doc)
	), alert=True, indicator="green")


@frappe.whitelist()
def cancel_task(task):
	task_doc = frappe.get_doc("Task", task)
	task_doc.check_permission("write")

	if task_doc.status != "Open":
		frappe.throw(_("Cannot cancel {0} because its status is {1}").format(
			get_link(task_doc),
			frappe.bold(task_doc.status)
		))

	task_doc.status = "Cancelled"
	task_doc.save()

	frappe.msgprint(_("{0} cancelled").format(
		get_link(task_doc)
	), indicator="green")


@frappe.whitelist()
def reopen_task(task):
	task_doc = frappe.get_doc("Task", task)
	task_doc.check_clocking_permission()

	if task_doc.status not in ["Completed", "Cancelled"]:
		frappe.throw(_("Cannot resume {0} because its status is {1}").format(
			get_link(task_doc),
			frappe.bold(task_doc.status)
		))

	check_project_not_ready_to_close(task_doc, _("re-open"))

	if task_doc.status == "Cancelled":
		task_doc.status = "Open"
	else:
		task_doc.status = "On Hold"

	task_doc.save(ignore_permissions=True)

	frappe.msgprint(_("{0} resumed").format(
		get_link(task_doc)
	), alert=True, indicator="green")


@frappe.whitelist()
def edit_task(task, subject, task_type=None, description=None, expected_time=None):
	task_doc = frappe.get_doc("Task", task)
	task_doc.check_permission("write")

	if not subject:
		frappe.throw(_("Subject is mandatory"))

	if task_doc.status == "Completed":
		frappe.throw(_("Cannot edit {0} because its status is {1}").format(
			get_link(task_doc),
			frappe.bold(task_doc.status)
		))

	check_project_not_ready_to_close(task_doc, _("edit"))

	task_doc.subject = subject
	task_doc.task_type = task_type
	task_doc.description = description
	if flt(expected_time):
		task_doc.expected_time = flt(expected_time)

	task_doc.save()

	frappe.msgprint(_("{0} edited").format(
		get_link(task_doc)
	), alert=True, indicator="green")


@frappe.whitelist()
def split_task(task, expected_time=None):
	frappe.has_permission("Task", "create", throw=True)

	ref_task = frappe.get_doc("Task", task)
	ref_task.check_permission("write")

	# Update expected time
	new_expected_time = flt(expected_time, ref_task.precision("expected_time"))
	previous_expected_time = flt(ref_task.expected_time, ref_task.precision("expected_time"))
	if new_expected_time and new_expected_time != previous_expected_time:
		ref_task.expected_time = new_expected_time
		ref_task.save()

	copy_fields = [
		"subject", "task_type", "project", "issue", "description",
		"priority", "service_template", "service_template_detail",
		"expected_time", "weight", "color", "is_milestone", "task_weight",
		"exp_start_date", "exp_end_date", "is_group", "company", "branch",
	]

	new_task = frappe.new_doc("Task")
	for f in copy_fields:
		new_task.set(f, ref_task.get(f))

	new_task.save()

	frappe.msgprint(_("{0} split from {1}").format(
		get_link(new_task), get_link(ref_task)
	), indicator="green")


def add_timesheet_log(task, assigned_to, project=None):
	filters = {
		"employee": assigned_to,
		"docstatus": 0,
	}

	if project:
		filters["project"] = project
	else:
		filters["task"] = project
		filters["project"] = ["is", "not set"]

	existing_timesheet = frappe.get_all("Timesheet", filters=filters, pluck="name")

	if existing_timesheet:
		ts_doc = frappe.get_doc("Timesheet", existing_timesheet[0])
	else:
		ts_doc = frappe.new_doc("Timesheet")
		ts_doc.employee = assigned_to

	ts_doc.append("time_logs", {
		"task": task,
		"project": project,
		"from_time": get_datetime(),
		"to_time": None,
	})

	ts_doc.flags.do_not_update_task = True
	ts_doc.save(ignore_permissions=True)


def stop_timesheet_log(task, assigned_to, completed):
	running_timesheets = frappe.db.sql_list("""
		SELECT distinct ts.name
		FROM `tabTimesheet Detail` tsd
		INNER JOIN tabTimesheet ts ON ts.name = tsd.parent
		WHERE ifnull(tsd.to_time, '') = ''
			AND ts.employee = %(assigned_to)s
			AND tsd.task = %(task)s
	""", {
		"task": task,
		"assigned_to": assigned_to,
	})

	for name in running_timesheets:
		ts_doc = frappe.get_doc("Timesheet", name)

		running_task_logs = [tl for tl in ts_doc.time_logs if tl.task == task and not tl.to_time]
		for tl in running_task_logs:
			tl.to_time = now_datetime()
			tl.completed = cint(completed)

		ts_doc.flags.do_not_update_task = True
		ts_doc.save(ignore_permissions=True)


def check_assigned_to_availability(employee, throw=False):
	working_task = frappe.db.get_value("Task", {"assigned_to": employee, "status": "Working"})
	if working_task:
		if throw:
			frappe.throw(_("{0} ({1}) is already working on {2}").format(
				frappe.bold(frappe.get_cached_value("Employee", employee, "employee_name")),
				employee,
				get_link_from_name("Task", working_task)
			))

		return False

	return True


def check_project_not_ready_to_close(task_doc, action_label):
	if not task_doc.project:
		return

	ready_to_close = frappe.db.get_value("Project", task_doc.project, "ready_to_close", cache=1)

	if ready_to_close:
		frappe.throw(_("Cannot {0} {1} because {2} is Ready to Close").format(
			action_label,
			get_link(task_doc),
			get_link_from_name("Project", task_doc.project)
		))


def check_project_set_in_task(task_doc):
	if not task_doc.project:
		frappe.throw(_("{0} is not against any {1}").format(
			get_link(task_doc),
			_("Project")
		))


def get_link(doc):
	return get_link_from_name(doc.doctype, doc.name, doc)


def get_link_from_name(doctype, name, doc=None):
	if doctype == "Task":
		subject = doc.subject if doc else frappe.db.get_value("Task", name, "subject")
		return f"<a href='{get_url_to_form(doctype, name)}'>{subject} ({name})</a>"
	else:
		return frappe.get_desk_link(doctype, name)


@frappe.whitelist()
def get_task_action_conditions(task):
	if isinstance(task, str):
		task = frappe.get_doc("Task", task)

	project = frappe._dict()
	if task.project:
		project = frappe.get_doc("Project", task.project)

	return _get_task_action_conditions(task=task, project=project)


def _get_task_action_conditions(task, project=None):
	has_task_create = frappe.has_permission("Task", "create")
	has_task_write = frappe.has_permission("Task", "write")
	can_clock_task = has_task_clocking_permission(task.assigned_to)

	project = project or frappe._dict()

	action_conditions = frappe._dict({
		"start_task": can_clock_task and task.assigned_to and task.status == "Open",
		"complete_task": can_clock_task and task.status in ("On Hold", "Working"),
		"pause_task": can_clock_task and task.status == "Working",
		"resume_task": can_clock_task and task.status == "On Hold",
		"reopen_task": can_clock_task and task.status in ("Completed", "Cancelled") and not project.ready_to_close,

		"edit_task": has_task_write and task.status != "Completed",
		"split_task": has_task_create and task.status not in ("Completed", "Cancelled"),
		"cancel_task": has_task_write and task.status == "Open",
	})

	frappe.utils.call_hook_method(
		"update_task_action_conditions",
		action_conditions=action_conditions,
		task=task,
		project=project,
	)

	return action_conditions


def has_task_clocking_permission(assigned_to):
	return frappe.has_permission("Task", "write") or is_assigned_employee(assigned_to)


def is_assigned_employee(assigned_to):
	def generator():
		employee = get_employee_from_user()
		return employee and assigned_to == employee

	if not assigned_to:
		return False
	else:
		return frappe.local_cache("is_assigned_employee", assigned_to, generator)


def get_task_status_color(status):
	return task_status_color_map.get(status, 'black')


def set_hrs_for_running_timelogs(timelogs):
	for tl in timelogs:
		if tl.from_time and not tl.to_time:
			tl.hours = (now_datetime() - get_datetime(tl.from_time)).total_seconds() / 3600


def add_tasks_actual_time_for_running_timelogs(tasks, timelogs):
	timesheet_task_map = {}
	for tl in timelogs:
		timesheet_task_map.setdefault(tl.task, []).append(tl)

	for task in tasks:
		for tl in timesheet_task_map.get(task.get("task") or task.get("name"), []):
			if tl.from_time and not tl.to_time:
				hours = (now_datetime() - get_datetime(tl.from_time)).total_seconds() / 3600
				task.actual_time += hours


def get_timelog_totals(timelogs):
	# assumes sorted by from_time
	return frappe._dict({
		"from_time": timelogs[0].from_time if timelogs else None,
		"to_time": timelogs[-1].to_time if timelogs and all(tl.to_time for tl in timelogs) else None,
		"hours": sum(flt(tl.hours) for tl in timelogs),
	})


def on_doctype_update():
	frappe.db.add_index("Task", ["lft", "rgt"])
