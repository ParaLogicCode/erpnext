# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import throw, _, scrub
from frappe.utils import getdate, validate_email_address, today, add_years, cstr, clean_whitespace
from frappe.model.naming import set_name_by_naming_series
from frappe.permissions import add_user_permission, remove_user_permission, has_permission
from erpnext.utilities.transaction_base import delete_events
from frappe.utils.nestedset import NestedSet


class EmployeeUserDisabledError(frappe.ValidationError): pass
class EmployeeLeftValidationError(frappe.ValidationError): pass


class Employee(NestedSet):
	nsm_parent_field = 'reports_to'

	def autoname(self):
		naming_method = frappe.db.get_value("HR Settings", None, "emp_created_by")
		if not naming_method:
			throw(_("Please setup Employee Naming System in Human Resource > HR Settings"))
		else:
			if naming_method == 'Naming Series':
				set_name_by_naming_series(self)
			elif naming_method == 'Employee Number':
				self.name = self.employee_number
			elif naming_method == 'Full Name':
				self.set_employee_name()
				self.name = self.employee_name

		self.employee = self.name

	def validate(self):
		from erpnext.controllers.status_updater import validate_status
		validate_status(self.status, ["Active", "Temporary Leave", "Left", "Inactive"])

		from frappe.regional.regional import validate_tax_ids
		validate_tax_ids(tax_id=self.tax_id, tax_cnic=self.tax_cnic)

		self.previous_attendance_device_id = cstr(self.db_get("attendance_device_id")) if not self.is_new() else ""

		self.employee = self.name
		self.set_employee_name()
		self.validate_date()
		self.validate_email()
		self.validate_status()
		self.validate_reports_to()
		self.validate_preferred_email()

		if self.job_applicant:
			self.validate_onboarding_process()

		if self.user_id:
			self.validate_user_details()
		else:
			existing_user_id = frappe.db.get_value("Employee", self.name, "user_id")
			if existing_user_id:
				remove_user_permission("Employee", self.name, existing_user_id)

	def set_employee_name(self):
		self.first_name = clean_whitespace(self.first_name)
		self.middle_name = clean_whitespace(self.middle_name)
		self.last_name = clean_whitespace(self.last_name)
		self.employee_name = ' '.join(filter(lambda x: x, [self.first_name, self.middle_name, self.last_name]))

	def validate_user_details(self):
		data = frappe.db.get_value('User',
			self.user_id, ['enabled', 'user_image'], as_dict=1)
		if data.get("user_image"):
			self.image = data.get("user_image")
		self.validate_for_enabled_user_id(data.get("enabled", 0))
		self.validate_duplicate_user_id()

	def update_nsm_model(self):
		frappe.utils.nestedset.update_nsm(self)

	def on_update(self):
		self.update_nsm_model()
		self.update_user()
		self.update_user_permissions()
		self.update_employee_checkins()
		self.update_sales_person()
		self.reset_employee_emails_cache()

	def update_user_permissions(self):
		if not self.user_id or not self.create_user_permission:
			return
		if not has_permission('User Permission', ptype='write', raise_exception=False):
			return

		employee_user_permission_exists = frappe.db.exists('User Permission', {
			'allow': 'Employee',
			'for_value': self.name,
			'user': self.user_id
		})
		company_user_permission_exists = frappe.db.exists('User Permission', {
			'allow': 'Company',
			'for_value': self.company,
			'user': self.user_id
		})

		if not employee_user_permission_exists:
			add_user_permission("Employee", self.name, self.user_id)
		if not company_user_permission_exists:
			add_user_permission("Company", self.company, self.user_id)

	def update_user(self):
		if not self.user_id:
			return

		# add employee role if missing
		user = frappe.get_doc("User", self.user_id)
		user.flags.ignore_permissions = True

		if "Employee" not in [d.role for d in user.get("roles")]:
			if not frappe.get_cached_value("Role", "Employee", "disabled"):
				user.append_roles("Employee")

		# copy details like Fullname, DOB and Image to User
		if self.employee_name and not (user.first_name and user.last_name):
			employee_name = self.employee_name.split(" ")
			if len(employee_name) >= 3:
				user.last_name = " ".join(employee_name[2:])
				user.middle_name = employee_name[1]
			elif len(employee_name) == 2:
				user.last_name = employee_name[1]

			user.first_name = employee_name[0]

		if self.date_of_birth:
			user.birth_date = self.date_of_birth

		if self.gender:
			user.gender = self.gender

		if self.image:
			if not user.user_image:
				user.user_image = self.image
				try:
					frappe.get_doc({
						"doctype": "File",
						"file_name": self.image,
						"attached_to_doctype": "User",
						"attached_to_name": self.user_id
					}).insert()
				except frappe.DuplicateEntryError:
					# already exists
					pass

		user.save()

	def update_employee_checkins(self):
		from erpnext.hr.doctype.employee_checkin.employee_checkin import update_employee_for_attendance_device_id

		if self.get("previous_attendance_device_id") is None:
			return
		if cstr(self.attendance_device_id) == cstr(self.previous_attendance_device_id):
			return

		if self.previous_attendance_device_id:
			update_employee_for_attendance_device_id(self.previous_attendance_device_id, None)
		if self.attendance_device_id:
			update_employee_for_attendance_device_id(self.attendance_device_id, self.name)

	def update_sales_person(self):
		sales_person_name = frappe.db.get_value("Sales Person", {"Employee": self.name})
		if not sales_person_name:
			return

		sales_person_doc = frappe.get_doc("Sales Person", sales_person_name)
		sales_person_doc.set_employee_details(update=True)
		sales_person_doc.notify_update()

	def validate_date(self):
		date_of_joining = self.date_of_joining if self.date_of_joining else getdate(self.creation)

		if self.date_of_birth and getdate(self.date_of_birth) > getdate(today()):
			throw(_("Date of Birth cannot be greater than today."))

		if self.date_of_birth and date_of_joining and getdate(self.date_of_birth) >= getdate(date_of_joining):
			throw(_("Date of Joining must be greater than Date of Birth"))

		elif self.date_of_retirement and date_of_joining and (getdate(self.date_of_retirement) <= getdate(date_of_joining)):
			throw(_("Date of Retirement must be greater than Date of Joining"))

		elif self.relieving_date and date_of_joining and (getdate(self.relieving_date) <= getdate(date_of_joining)):
			throw(_("Relieving Date must be greater than Date of Joining"))

		elif self.contract_end_date and date_of_joining and (getdate(self.contract_end_date) <= getdate(date_of_joining)):
			throw(_("Contract End Date must be greater than Date of Joining"))

	def validate_email(self):
		if self.company_email:
			validate_email_address(self.company_email, True)
		if self.personal_email:
			validate_email_address(self.personal_email, True)

	def validate_status(self):
		if self.status == 'Left':
			reports_to = frappe.db.get_all('Employee',
				filters={'reports_to': self.name, 'status': "Active"},
				fields=['name','employee_name']
			)
			if reports_to:
				link_to_employees = [frappe.utils.get_link_to_form('Employee', employee.name, label=employee.employee_name) for employee in reports_to]
				throw(_("Employee status cannot be set to 'Left' as following employees are currently reporting to this employee:&nbsp;")
					+ ', '.join(link_to_employees), EmployeeLeftValidationError)
			if not self.relieving_date:
				throw(_("Please enter relieving date."))

	def validate_for_enabled_user_id(self, enabled):
		if not self.status == 'Active':
			return

		if enabled is None:
			frappe.throw(_("User {0} does not exist").format(self.user_id))
		if enabled == 0:
			frappe.throw(_("User {0} is disabled").format(self.user_id), EmployeeUserDisabledError)

	def validate_duplicate_user_id(self):
		employee = frappe.db.sql_list("""select name from `tabEmployee` where
			user_id=%s and status='Active' and name!=%s""", (self.user_id, self.name))
		if employee:
			throw(_("User {0} is already assigned to Employee {1}").format(
				self.user_id, employee[0]), frappe.DuplicateEntryError)

	def validate_reports_to(self):
		if self.reports_to == self.name:
			throw(_("Employee cannot report to himself."))

	def on_trash(self):
		self.update_nsm_model()
		delete_events(self.doctype, self.name)
		if frappe.db.exists("Employee Transfer", {'new_employee_id': self.name, 'docstatus': 1}):
			emp_transfer = frappe.get_doc("Employee Transfer", {'new_employee_id': self.name, 'docstatus': 1})
			emp_transfer.db_set("new_employee_id", '')

	def validate_preferred_email(self):
		if self.prefered_contact_email and not self.get(scrub(self.prefered_contact_email)):
			frappe.msgprint(_("Please enter " + self.prefered_contact_email))

	def validate_onboarding_process(self):
		employee_onboarding = frappe.get_all("Employee Onboarding",
			filters={"job_applicant": self.job_applicant, "docstatus": 1, "boarding_status": ("!=", "Completed")})
		if employee_onboarding:
			doc = frappe.get_doc("Employee Onboarding", employee_onboarding[0].name)
			doc.validate_employee_creation()
			doc.db_set("employee", self.name)

	def reset_employee_emails_cache(self):
		prev_doc = self.get_doc_before_save() or {}
		cell_number = cstr(self.get('cell_number'))
		prev_number = cstr(prev_doc.get('cell_number'))
		if (cell_number != prev_number or
			self.get('user_id') != prev_doc.get('user_id')):
			frappe.cache.hdel('employees_with_number', cell_number)
			frappe.cache.hdel('employees_with_number', prev_number)


def get_timeline_data(doctype, name):
	'''Return timeline for attendance'''
	return dict(frappe.db.sql('''
		select unix_timestamp(attendance_date), sum(if(status = 'Half Day', 0.5, 1))
		from `tabAttendance`
		where employee=%s
			and attendance_date > date_sub(curdate(), interval 1 year)
			and status in ('Present', 'Half Day')
			and docstatus = 1
		group by attendance_date
	''', name))


@frappe.whitelist()
def get_retirement_date(date_of_birth=None):
	ret = {}
	if date_of_birth:
		try:
			retirement_age = int(frappe.db.get_single_value("HR Settings", "retirement_age") or 60)
			dt = add_years(getdate(date_of_birth),retirement_age)
			ret = {'date_of_retirement': dt.strftime('%Y-%m-%d')}
		except ValueError:
			# invalid date
			ret = {}

	return ret


def validate_employee_role(doc, method):
	# called via User hook
	if "Employee" in [d.role for d in doc.get("roles")]:
		if not frappe.db.get_value("Employee", {"user_id": doc.name}):
			frappe.msgprint(_("Please set User ID field in an Employee record to set Employee Role"))
			doc.get("roles").remove(doc.get("roles", {"role": "Employee"})[0])


def update_user_permissions(doc, method):
	# called via User hook
	if "Employee" in [d.role for d in doc.get("roles")]:
		if not has_permission('User Permission', ptype='write', raise_exception=False): return
		employee = frappe.get_doc("Employee", {"user_id": doc.name})
		employee.update_user_permissions()


def get_holiday_list_for_employee(employee, raise_exception=True):
	from erpnext.hr.doctype.holiday_list.holiday_list import get_default_holiday_list

	emp_details = None
	if employee:
		emp_details = frappe.db.get_value("Employee", employee, ("holiday_list", "company"), cache=1)

	if emp_details:
		holiday_list, company = emp_details
	else:
		holiday_list = None
		company = frappe.db.get_single_value("Global Defaults", "default_company")

	if not holiday_list:
		holiday_list = get_default_holiday_list(company)

	if not holiday_list and raise_exception:
		frappe.throw(_('Please set a default Holiday List for Employee {0} or Company {1}').format(employee, company))

	return holiday_list


def is_holiday(employee, date=None, raise_exception=True):
	'''Returns True if given Employee has an holiday on the given date
	:param employee: Employee `name`
	:param date: Date to check. Will check for today if None'''

	holiday_list = get_holiday_list_for_employee(employee, raise_exception)
	if not date:
		date = today()

	if holiday_list:
		return frappe.get_all('Holiday List', dict(name=holiday_list, holiday_date=date)) and True or False


@frappe.whitelist()
def create_user(employee, user = None, email=None):
	emp = frappe.get_doc("Employee", employee)

	employee_name = emp.employee_name.split(" ")
	middle_name = last_name = ""

	if len(employee_name) >= 3:
		last_name = " ".join(employee_name[2:])
		middle_name = employee_name[1]
	elif len(employee_name) == 2:
		last_name = employee_name[1]

	first_name = employee_name[0]

	if email:
		emp.prefered_email = email

	user = frappe.new_doc("User")
	user.update({
		"name": emp.employee_name,
		"email": emp.prefered_email,
		"enabled": 1,
		"first_name": first_name,
		"middle_name": middle_name,
		"last_name": last_name,
		"gender": emp.gender,
		"birth_date": emp.date_of_birth,
		"phone": emp.cell_number,
		"bio": emp.bio
	})
	user.insert()
	return user.name


def get_employee_emails(employee_list):
	'''Returns list of employee emails either based on user_id or company_email'''
	employee_emails = []
	for employee in employee_list:
		if not employee:
			continue
		user, company_email, personal_email = frappe.db.get_value('Employee', employee,
											['user_id', 'company_email', 'personal_email'])
		email = user or company_email or personal_email
		if email:
			employee_emails.append(email)
	return employee_emails


@frappe.whitelist()
def get_children(doctype, parent=None, company=None, is_root=False, is_tree=False):
	filters = [['company', '=', company]]
	fields = ['name as value', 'employee_name as title']

	if is_root:
		parent = ''
	if parent and company and parent!=company:
		filters.append(['reports_to', '=', parent])
	else:
		filters.append(['reports_to', '=', ''])

	employees = frappe.get_list(doctype, fields=fields,
		filters=filters, order_by='name')

	for employee in employees:
		is_expandable = frappe.get_all(doctype, filters=[
			['reports_to', '=', employee.get('value')]
		])
		employee.expandable = 1 if is_expandable else 0

	return employees


def on_doctype_update():
	frappe.db.add_index("Employee", ["lft", "rgt"])


def has_user_permission_for_employee(user_name, employee_name):
	return frappe.db.exists({
		'doctype': 'User Permission',
		'user': user_name,
		'allow': 'Employee',
		'for_value': employee_name
	})


def get_employee_from_user(user):
	employee_docname = frappe.db.get_value('Employee', filters={'user_id': user})
	return employee_docname


def send_employee_birthday_notification():
	date_today = getdate()

	employees = get_employees_who_have_birthday_today(date_today)
	for name in employees:
		doc = frappe.get_doc("Employee", name)
		if doc.date_of_birth:
			date_of_birth = getdate(doc.date_of_birth)
			doc.age = date_today.year - date_of_birth.year
			doc.run_method("send_birthday_notification")


def get_employees_who_have_birthday_today(date_today=None):
	date_today = getdate(date_today)

	employee_birthday_data = frappe.db.sql_list("""
		SELECT name
		FROM tabEmployee
		WHERE day(date_of_birth) = %s
		AND month(date_of_birth) = %s
		AND status = 'Active'
	""", [date_today.day, date_today.month])

	return employee_birthday_data


def send_employee_anniversary_notification():
	date_today = getdate()

	employees = get_employees_who_have_anniversary_today(date_today)
	for name in employees:
		doc = frappe.get_doc("Employee", name)
		if doc.date_of_joining:
			date_of_joining = getdate(doc.date_of_joining)
			doc.number_of_years = date_today.year - date_of_joining.year
			doc.run_method("send_anniversary_notification")


def get_employees_who_have_anniversary_today(date_today=None):
	date_today = getdate(date_today)

	employee_anniversary_data = frappe.db.sql_list("""
		SELECT name
		FROM tabEmployee
		WHERE day(date_of_joining) = %s
		AND month(date_of_joining) = %s
		AND year(date_of_joining) < %s
		AND status = 'Active'
	""", [date_today.day, date_today.month, date_today.year])

	return employee_anniversary_data
