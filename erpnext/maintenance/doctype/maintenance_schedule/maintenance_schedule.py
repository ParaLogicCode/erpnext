# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from erpnext.utilities.transaction_base import TransactionBase
from dateutil.relativedelta import relativedelta
from frappe.utils import add_days, getdate, get_time, now_datetime, combine_datetime, add_to_date, cstr, cint
from frappe.contacts.doctype.contact.contact import get_default_contact
from erpnext.accounts.party import get_contact_details
from frappe.core.doctype.sms_settings.sms_settings import enqueue_template_sms


class MaintenanceSchedule(TransactionBase):
	def validate(self):
		self.set_missing_values()
		self.validate_serial_no()
		self.validate_schedule()

	def set_missing_values(self):
		self.set_contact_details()

	def set_contact_details(self):
		force = False
		if not self.contact_person and self.customer:
			contact = get_default_contact('Customer', self.customer)
			self.contact_person = contact
			force = True

		if self.contact_person:
			contact_details = get_contact_details(self.contact_person)
			for k, v in contact_details.items():
				if self.meta.has_field(k) and (force or not self.get(k)):
					self.set(k, v)

	def validate_serial_no(self):
		if self.serial_no:
			self.item_code, self.item_name = frappe.db.get_value("Serial No", self.serial_no, ["item_code", "item_name"])

	def validate_schedule(self):
		self.sort_schedules()
		date_template_pairs = set()

		for d in self.schedules:
			date_template_pair = (d.scheduled_date, cstr(d.project_template))
			if date_template_pair not in date_template_pairs:
				date_template_pairs.add(date_template_pair)
			else:
				frappe.throw(_("Row {0}: Duplicate schedule found".format(d.idx)))

	def sort_schedules(self):
		self.schedules.sort(key=lambda x: x.get('scheduled_date'))
		for index, d in enumerate(self.schedules):
			d.idx = index + 1

	def adjust_scheduled_date_for_holiday(self, scheduled_date):
		from erpnext.hr.doctype.holiday_list.holiday_list import get_default_holiday_list

		holiday_list_name = get_default_holiday_list(self.company)

		if holiday_list_name:
			holiday_dates = frappe.db.sql_list("select holiday_date from `tabHoliday` where parent=%s", holiday_list_name)
			if holiday_dates:
				scheduled_date = getdate(scheduled_date)
				while scheduled_date in holiday_dates:
					scheduled_date = add_days(scheduled_date, -1)

		return scheduled_date

	def send_maintenance_schedule_reminder_notification(self, row_name):
		msd_doctype = "Maintenance Schedule Detail"
		ms_row = [d for d in self.schedules if d.name == row_name]
		if not ms_row:
			frappe.throw(_("Invalid Maintenance Schedule"))

		ms_row = ms_row[0]
		context = {'row': ms_row}
		enqueue_template_sms(self, "Maintenance Reminder", context=context, child_doctype=msd_doctype, child_name=row_name)

	def validate_notification(self, notification_type=None, child_doctype=None, child_name=None, throw=False):
		if not notification_type:
			if throw:
				frappe.throw(_("Notification Type is mandatory"))
			return False

		if notification_type in ("Maintenance Reminder"):
			ms_row = [d for d in self.schedules if d.name == child_name]
			if not ms_row:
				frappe.throw(_("Invalid Maintenance Schedule"))
			ms_row = ms_row[0]

			if not ms_row.scheduled_date:
				if throw:
					frappe.throw(_("Scheduled Date not found"))
				return False

			if getdate(ms_row.scheduled_date) < getdate():
				if throw:
					frappe.throw(_("Cannot send {0} notification after Scheduled Date has passed")
						.format(notification_type))
				return False

			if self.status != "Active":
				if throw:
					frappe.throw(_("Cannot send {0} notification because Maintenance Schedule status is not 'Active'")
						.format(notification_type))
				return False
		return True

	def get_sms_args(self, notification_type=None, child_doctype=None, child_name=None):
		sms_args = frappe._dict({
			'receiver_list' : [self.contact_mobile],
			'party_doctype': 'Customer',
			'party': self.customer,
		})

		return sms_args


def auto_schedule_next_project_templates():
	if not frappe.db.get_single_value("Projects Settings", "auto_schedule_next_project_templates"):
		return

	run_date = getdate()
	schedule_date = add_to_date(date=run_date, days=-1)

	schedule_data = frappe.db.sql("""
		select msd.project_template, ms.serial_no
		from `tabMaintenance Schedule Detail` msd
		inner join `tabMaintenance Schedule` ms on ms.name = msd.parent
		inner join `tabProject Template` pt on pt.name = msd.project_template
		where
			msd.scheduled_date = %s
			and ms.status = 'Active'
			and ifnull(ms.serial_no, '') != ''
			and ifnull(pt.next_project_template, '') != ''
	""", schedule_date, as_dict=1)

	for schedule in schedule_data:
		schedule_next_project_template(
			schedule.project_template,
			schedule.serial_no,
			args={"reference_date": schedule_date},
			overwrite_existing=False
		)


def schedule_next_project_template(project_template, serial_no, args=None, overwrite_existing=True):
	if not project_template:
		return

	args = frappe._dict(args or {})

	template_details = frappe.get_cached_value("Project Template", project_template, ["next_due_after", "next_project_template"], as_dict=1)
	if not template_details or not template_details.next_due_after or not template_details.next_project_template:
		return

	doc = get_maintenance_schedule_doc(serial_no)

	schedule = frappe._dict({
		'project_template': template_details.next_project_template,
		'reference_doctype': args.reference_doctype,
		'reference_name': args.reference_name,
		'reference_date': getdate(args.reference_date)
	})
	schedule.scheduled_date = schedule.reference_date + relativedelta(months=template_details.next_due_after)

	existing_row = [
		d for d in doc.get('schedules')
		if d.get("project_template") == template_details.next_project_template
		and d.get("scheduled_date") >= schedule.reference_date
	]
	existing_row = existing_row[0] if existing_row else None
	if existing_row and not overwrite_existing:
		return

	schedule.scheduled_date = doc.adjust_scheduled_date_for_holiday(schedule.scheduled_date)
	if existing_row:
		existing_row.update(schedule)
	else:
		doc.append('schedules', schedule)

	update_customer_and_contact(args, doc)
	doc.save(ignore_permissions=True)


def schedule_project_templates_after_delivery(serial_no, args):
	item_code = frappe.db.get_value("Serial No", serial_no, "item_code")
	if not item_code:
		return

	args = frappe._dict(args)
	if not args.reference_doctype or not args.reference_name:
		frappe.throw(_("Invalid reference for Maintenance Schedule after Delivery"))

	schedule_template = frappe._dict({
		'reference_doctype': args.reference_doctype,
		'reference_name': args.reference_name,
		'reference_date': getdate(args.reference_date)
	})

	project_templates = get_project_templates_due_after_delivery(item_code)

	doc = get_maintenance_schedule_doc(serial_no)
	modified = False

	update_customer_and_contact(args, doc)

	existing_templates = [d.get('project_template') for d in doc.get('schedules', []) if d.get('project_template')]

	for d in project_templates:
		if d.name not in existing_templates:
			schedule = schedule_template.copy()
			schedule.project_template = d.name
			schedule.scheduled_date = schedule.reference_date + relativedelta(months=d.due_after_delivery_date)
			schedule.scheduled_date = doc.adjust_scheduled_date_for_holiday(schedule.scheduled_date)
			doc.append('schedules', schedule)

			modified = True

	if modified:
		doc.save(ignore_permissions=True)


def remove_schedule_for_reference_document(serial_no, reference_doctype, reference_name):
	doc = get_maintenance_schedule_doc(serial_no)

	if not doc.get('schedules'):
		return

	to_remove = [d for d in doc.schedules if d.reference_doctype == reference_doctype and d.reference_name == reference_name]
	if to_remove:
		for d in to_remove:
			doc.remove(d)

		doc.save(ignore_permissions=True)


def get_project_templates_due_after_delivery(item_code):
	filters = {'due_after_delivery_date': ['>', 0]}

	fields = ['name', 'due_after_delivery_date']
	order_by = "due_after_delivery_date"

	filters['applies_to_item'] = item_code
	project_templates = frappe.get_all('Project Template', filters=filters, fields=fields, order_by=order_by)

	if not project_templates:
		variant_of = frappe.get_cached_value("Item", item_code, "variant_of")
		if variant_of:
			filters["applies_to_item"] = variant_of
			project_templates = frappe.get_all('Project Template', filters=filters, fields=fields, order_by=order_by)

	return project_templates


def get_maintenance_schedule_doc(serial_no):
	schedule_name = frappe.db.get_value('Maintenance Schedule', filters={'serial_no': serial_no})

	if schedule_name:
		doc = frappe.get_doc('Maintenance Schedule', schedule_name)
	else:
		doc = frappe.new_doc('Maintenance Schedule')
		doc.serial_no = serial_no
		doc.item_code, doc.item_name = frappe.db.get_value("Serial No", serial_no, ["item_code", "item_name"])

	return doc


def update_customer_and_contact(source, target_doc):
	customer_fields = ['customer', 'customer_name']
	contact_fields = ['contact_person', 'contact_display', 'contact_mobile', 'contact_phone', 'contact_email']

	if source.customer:
		for f in customer_fields:
			target_doc.set(f, source.get(f))

		for f in contact_fields:
			target_doc.set(f, None)

	if source.contact_person:
		for f in contact_fields:
			target_doc.set(f, source.get(f))


def get_maintenance_schedule_from_serial_no(serial_no):
	schedule_name = frappe.db.get_value('Maintenance Schedule', filters={'serial_no': serial_no})

	if schedule_name:
		schedule_doc = frappe.get_doc('Maintenance Schedule', schedule_name)
		return schedule_doc.schedules


def create_opportunity_from_schedule(for_date=None):
	if not frappe.db.get_single_value("CRM Settings", "auto_create_opportunity_from_schedule"):
		return

	days_in_advance = frappe.get_cached_value("CRM Settings", None, "maintenance_opportunity_reminder_days")

	for_date = getdate(for_date)
	target_date = getdate(add_days(for_date, days_in_advance))

	schedule_data = frappe.db.sql("""
		select msd.name, msd.parent, msd.project_template
		from `tabMaintenance Schedule Detail` msd
		inner join `tabMaintenance Schedule` ms on ms.name = msd.parent
		where ms.status = 'Active' and msd.scheduled_date = %s
			and not exists(select opp.name from `tabOpportunity` opp
				where opp.maintenance_schedule = ms.name
					and opp.maintenance_schedule_row = msd.name
			)
	""", target_date, as_dict=1)

	for schedule in schedule_data:
		opportunity_doc = create_maintenance_opportunity(schedule.parent, schedule.name)
		opportunity_doc.flags.ignore_mandatory = True
		opportunity_doc.save(ignore_permissions=True)


def get_maintenance_schedule_opportunity(maintenance_schedule, row):
	maintenance_opp = frappe.db.get_value("Opportunity", filters={
		'maintenance_schedule':maintenance_schedule,
		'maintenance_schedule_row': row
	})

	if maintenance_opp:
		return frappe.get_doc('Opportunity', maintenance_opp)
	else:
		return create_maintenance_opportunity(maintenance_schedule, row)


@frappe.whitelist()
def create_maintenance_opportunity(maintenance_schedule, row):
	schedule_doc = frappe.get_doc('Maintenance Schedule', maintenance_schedule)
	default_opportunity_type = frappe.get_cached_value("CRM Settings", None, "default_opportunity_type_for_schedule")
	schedule = schedule_doc.getone('schedules', {'name': row})

	if not schedule:
		frappe.throw(_("Invalid Maintenance Schedule Row Provided"))

	target_doc = frappe.new_doc('Opportunity')

	target_doc.opportunity_from = 'Customer'
	target_doc.party_name = schedule_doc.customer
	target_doc.transaction_date = getdate()
	target_doc.due_date = schedule.scheduled_date
	target_doc.status = 'Open'
	target_doc.opportunity_type = default_opportunity_type
	target_doc.applies_to_serial_no = schedule_doc.serial_no

	target_doc.maintenance_schedule = schedule_doc.name
	target_doc.maintenance_schedule_row = schedule.name

	if schedule.project_template:
		project_template = frappe.get_cached_doc('Project Template', schedule.project_template)
		for d in project_template.sales_items:
			target_doc.append("items", {
				"item_code": d.applicable_item_code,
				"qty": d.applicable_qty,
			})

	target_doc.run_method("set_missing_values")
	target_doc.run_method("validate_maintenance_schedule")
	return target_doc


def send_maintenance_schedule_reminder_notifications():
	if not automated_maintenance_reminder_enabled():
		return

	now_dt = now_datetime()
	reminder_date = getdate(now_dt)
	reminder_dt = get_maintenance_reminder_scheduled_time(reminder_date)
	if now_dt < reminder_dt:
		return

	notification_last_sent_date = frappe.db.get_global("maintenance_schedule_notification_last_sent_date")
	if notification_last_sent_date and getdate(notification_last_sent_date) >= reminder_date:
		return

	schedules_to_remind = get_maintenance_schedules_for_reminder_notification(reminder_date)

	for d in schedules_to_remind:
		doc = frappe.get_doc("Maintenance Schedule", d.ms_name)
		doc.send_maintenance_schedule_reminder_notification(d.row_name)

	frappe.db.set_global("maintenance_schedule_notification_last_sent_date", reminder_date)


def get_maintenance_schedules_for_reminder_notification(reminder_date=None):
	reminder_date = getdate(reminder_date)

	remind_days_before = cint(frappe.db.get_single_value("CRM Settings", "maintenance_reminder_days_before"))
	if remind_days_before < 1:
		return

	schedule_date = add_days(reminder_date, remind_days_before)

	schedule_to_remind = frappe.db.sql("""
		SELECT ms.name AS ms_name, msd.name AS row_name, msd.scheduled_date
		FROM `tabMaintenance Schedule` ms
		INNER JOIN `tabMaintenance Schedule Detail` msd ON msd.parent = ms.name
		LEFT JOIN `tabNotification Count` AS nc
			ON nc.reference_doctype =  'Maintenance Schedule' AND nc.reference_name = ms.name
			And nc.child_doctype = 'Maintenance Schedule Detail' AND nc.child_name = msd.name
		WHERE ms.status = 'Active'
			AND msd.scheduled_date = %(schedule_date)s
			AND nc.last_scheduled_dt is NULL
			AND %(reminder_date)s <= msd.scheduled_date
			AND (nc.last_sent_dt is null or DATE(nc.last_sent_dt) != %(reminder_date)s)
	""", {
		'schedule_date': schedule_date,
		'reminder_date': reminder_date,
	}, as_dict=1)

	return schedule_to_remind


def automated_maintenance_reminder_enabled():
	from frappe.core.doctype.sms_settings.sms_settings import is_automated_sms_enabled
	from frappe.core.doctype.sms_template.sms_template import has_automated_sms_template

	if is_automated_sms_enabled() and has_automated_sms_template("Maintenance Schedule", "Maintenance Reminder"):
		return True
	else:
		return False


def get_maintenance_reminder_scheduled_time(reminder_date=None):
	crm_settings = frappe.get_cached_doc("CRM Settings", None)
	reminder_date = getdate(reminder_date)
	reminder_time = crm_settings.maintenance_reminder_time or get_time("00:00:00")
	reminder_dt = combine_datetime(reminder_date, reminder_time)

	return reminder_dt


def get_reminder_date_from_schedule_date(schedule_date):
	crm_settings = frappe.get_cached_doc("CRM Settings", None)
	schedule_date = getdate(schedule_date)

	remind_days_before = cint(crm_settings.maintenance_reminder_days_before)
	if remind_days_before < 0:
		remind_days_before = 0

	reminder_date = add_days(schedule_date, -remind_days_before)
	return reminder_date
