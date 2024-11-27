import frappe
from frappe	import _
from frappe.utils import flt
from crm.crm.doctype.sales_person.sales_person import SalesPerson
from erpnext import get_default_currency
from erpnext.selling.doctype.sales_commission_category.sales_commission_category import get_commission_rate


class SalesPersonERP(SalesPerson):
	def onload(self):
		self.load_dashboard_info()

	def validate(self):
		self.validate_employee_id()
		self.set_employee_details()
		super().validate()
		self.validate_targets()

	def set_employee_details(self, update=False, update_modified=True):
		if not self.employee:
			self.employee_name = None
			return

		employee_details = get_employee_details(self.employee)

		self.update(employee_details)
		if update:
			self.db_set(employee_details, update_modified=update_modified)

	def validate_employee_id(self):
		if self.employee:
			sales_person = frappe.db.get_value("Sales Person", filters={
				"employee": self.employee, "name": ["!=", self.name]
			})
			if sales_person:
				frappe.throw(_("Another {0} exists linked to the same Employee {1}").format(
					frappe.get_desk_link("Sales Person", sales_person), frappe.bold(self.employee)
				))

	def validate_targets(self):
		for d in self.get('targets'):
			if not flt(d.target_qty) and not flt(d.target_alt_uom_qty) and not flt(d.target_amount):
				frappe.throw(_("Row {0}: Either Target Stock Qty or Target Contents Qty or Target Amount is mandatory.")
					.format(d.idx))

	def get_email_id(self):
		if self.employee:
			user = frappe.db.get_value("Employee", self.employee, "user_id")
			if not user:
				frappe.throw(_("User ID not set for Employee {0}").format(self.employee))
			else:
				return frappe.db.get_value("User", user, "email") or user

	def load_dashboard_info(self):
		company_default_currency = get_default_currency()

		allocated_amount = frappe.db.sql("""
			select sum(allocated_amount)
			from `tabSales Team`
			where sales_person = %s and docstatus=1 and parenttype = 'Sales Invoice'
		""", self.name)

		allocated_qty = frappe.db.sql("""
			select
				sum(inv.total_qty * steam.allocated_percentage / 100),
				sum(inv.total_alt_uom_qty * steam.allocated_percentage / 100)
			from `tabSales Team` steam
			inner join `tabSales Invoice` inv on steam.parent = inv.name
			where steam.sales_person = %s and inv.docstatus=1 and steam.parenttype = 'Sales Invoice'
		""", self.name)

		info = {
			"allocated_amount": flt(allocated_amount[0][0]) if allocated_amount else 0,
			"allocated_stock_qty": flt(allocated_qty[0][0]) if allocated_qty else 0,
			"allocated_alt_uom_qty": flt(allocated_qty[0][1]) if allocated_qty else 0,
			"currency": company_default_currency
		}

		self.set_onload('dashboard_info', info)

	@staticmethod
	def get_timeline_data(name):
		out = SalesPerson.get_timeline_data(name)

		sales_orders = dict(frappe.db.sql("""
			select unix_timestamp(dt.transaction_date), count(st.parenttype)
			from `tabSales Order` dt, `tabSales Team` st
			where st.sales_person = %s and st.parent = dt.name
				and dt.transaction_date > date_sub(curdate(), interval 1 year)
			group by dt.transaction_date
		""", name))

		delivery_notes = dict(frappe.db.sql("""
			select unix_timestamp(dt.posting_date), count(st.parenttype)
			from `tabDelivery Note` dt, `tabSales Team` st
			where st.sales_person = %s and st.parent = dt.name
				and dt.posting_date > date_sub(curdate(), interval 1 year)
			group by dt.posting_date
		""", name))

		sales_invoices = dict(frappe.db.sql("""
			select unix_timestamp(dt.posting_date), count(st.parenttype)
			from `tabSales Invoice` dt, `tabSales Team` st
			where st.sales_person = %s and st.parent = dt.name
				and dt.posting_date > date_sub(curdate(), interval 1 year)
			group by dt.posting_date
		""", name))

		for data in (sales_orders, delivery_notes, sales_invoices):
			for key, value in data.items():
				out.setdefault(key, 0)
				out[key] += value

		return out


@frappe.whitelist()
def get_sales_person_commission_details(sales_person=None):
	out = frappe._dict()

	if sales_person:
		out.sales_commission_category = frappe.get_cached_value("Sales Person", sales_person, 'sales_commission_category')
	else:
		out.sales_commission_category = None

	out.commission_rate = get_commission_rate(out.sales_commission_category)

	return out


@frappe.whitelist()
def get_employee_details(employee):
	employee_details = frappe.db.get_value("Employee", employee, [
		"employee_name", "branch", "department", "designation", "user_id",
		"cell_number", "prefered_email", "company_email", "personal_email",
	], as_dict=1) if employee else frappe._dict()

	out = frappe._dict()

	out.employee_name = employee_details.employee_name
	out.branch = employee_details.branch
	out.department = employee_details.department
	out.designation = employee_details.designation
	out.user_id = employee_details.user_id
	out.contact_mobile = employee_details.cell_number
	out.contact_email = employee_details.prefered_email or employee_details.company_email or employee_details.personal_email

	return out


def override_sales_person_dashboard(data):
	data["transactions"].insert(0, {
		"label": _("Sales"),
		"items": ["Sales Order", "Delivery Note", "Sales Invoice"]
	})

	data["transactions"].append({
		"label": _("Customers"),
		"items": ["Customer"]
	})

	return data
