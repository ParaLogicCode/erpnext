# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate


class ProjectSalesSummaryReport(object):
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or {})
		self.filters.from_date = getdate(self.filters.from_date)
		self.filters.to_date = getdate(self.filters.to_date)

	def run(self):
		self.get_data()
		columns = self.get_columns()
		return columns, self.data

	def get_data(self):
		conditions = self.get_conditions()
		conditions_str = " and ".join(conditions) if conditions else ""

		select_fields, joins = self.get_select_fields_and_joins()
		select_fields_str = ", ".join(select_fields)
		joins_str = " ".join(joins)

		self.data = frappe.db.sql(f"""
			select {select_fields_str}
			from `tabProject` p
			left join `tabItem` im on im.name = p.applies_to_item
			left join `tabCustomer` c on c.name = p.customer
			{joins_str}
			where {conditions_str}
			group by p.name
			order by p.project_date, p.creation
		""", self.filters, as_dict=1)

		for d in self.data:
			# Model Name if not a variant
			if not d.applies_to_variant_of_name:
				d.applies_to_variant_of_name = d.applies_to_item_name

	def get_select_fields_and_joins(self):
		select_fields = [
			"p.name as project", "p.project_name", "p.project_type",
			"p.project_date", "p.project_status", "p.project_workshop",
			"p.total_sales_amount", "p.stock_sales_amount", "p.part_sales_amount", "p.lubricant_sales_amount",
			"p.service_sales_amount", "p.labour_sales_amount", "p.sublet_sales_amount",
			"p.customer", "p.customer_name", "p.company",
			"p.service_advisor", "p.service_manager",
			"p.applies_to_variant_of", "p.applies_to_variant_of_name",
			"p.applies_to_item", "p.applies_to_item_name",
		]

		return select_fields, []

	def get_conditions(self):
		conditions = []

		if self.filters.get("company"):
			conditions.append("p.company = %(company)s")

		if self.filters.get("from_date"):
			conditions.append("p.project_date >= %(from_date)s")

		if self.filters.get("to_date"):
			conditions.append("p.project_date <= %(to_date)s")

		if self.filters.get("status"):
			if self.filters.get("status") == "Closed":
				conditions.append("status in ('Closed', 'Completed')")
			elif self.filters.get("status") == "Cancelled":
				conditions.append( "status = 'Cancelled'")
			elif self.filters.get("status") == "Open":
				conditions.append("status = 'Open'")
			elif self.filters.get("status") == "Closed or To Close":
				conditions.append("status in ('To Close', 'Closed', 'Completed')")
			elif self.filters.get("status") == "To Close":
				conditions.append("status = 'To Close'")
		else:
			conditions.append("status!='Cancelled'")

		if self.filters.get("project"):
			conditions.append("p.name = %(project)s")

		if self.filters.get("project_type"):
			conditions.append("p.project_type = %(project_type)s")

		if self.filters.get("customer"):
			conditions.append("p.customer=%(customer)s")

		if self.filters.get("customer_group"):
			lft, rgt = frappe.db.get_value("Customer Group", self.filters.customer_group, ["lft", "rgt"])
			conditions.append("""c.customer_group in (select name from `tabCustomer Group`
				where lft>=%s and rgt<=%s and docstatus<2)""" % (lft, rgt))

		if self.filters.get("applies_to_variant_of"):
			conditions.append("im.variant_of = %(applies_to_variant_of)s")

		if self.filters.get("applies_to_item"):
			conditions.append("p.applies_to_item = %(applies_to_item)s")

		if self.filters.project_workshop:
			conditions.append("p.project_workshop = %(project_workshop)s")

		if self.filters.service_advisor:
			conditions.append("p.service_advisor = %(service_advisor)s")

		if self.filters.service_manager:
			conditions.append("p.service_manager = %(service_manager)s")

		if self.filters.get("item_group"):
			lft, rgt = frappe.db.get_value("Item Group", self.filters.item_group, ["lft", "rgt"])
			conditions.append("""im.item_group in (select name from `tabItem Group`
				where lft>=%s and rgt<=%s and docstatus<2)""" % (lft, rgt))

		if self.filters.get("brand"):
			conditions.append("im.brand=%(brand)s")

		if self.filters.get("item_source"):
			conditions.append("im.item_source=%(item_source)s")

		if self.filters.get("territory"):
			lft, rgt = frappe.db.get_value("Territory", self.filters.territory, ["lft", "rgt"])
			conditions.append("""c.territory in (select name from `tabTerritory`
				where lft>=%s and rgt<=%s and docstatus<2)""" % (lft, rgt))

		return conditions

	def get_columns(self):
		columns = [
			{'label': _("Project"), 'fieldname': 'project', 'fieldtype': 'Link', 'options': 'Project', 'width': 100},
			{'label': _("Date"), 'fieldname': 'project_date', 'fieldtype': 'Date', 'width': 80},
			{'label': _("Project Type"), 'fieldname': 'project_type', 'fieldtype': 'Link', 'options': 'Project Type', 'width': 100},
			{'label': _("Status"), 'fieldname': 'project_status', 'fieldtype': 'Data', 'width': 100},
			{"label": _("Voice of Customer"), "fieldname": "project_name", "fieldtype": "Data", "width": 150},
			{'label': _("Customer"), 'fieldname': 'customer', 'fieldtype': 'Link', 'options': 'Customer', 'width': 100},
			{'label': _("Customer Name"), 'fieldname': 'customer_name', 'fieldtype': 'Data', 'width': 150},

			{'label': _("Materials Total"), 'fieldname': 'stock_sales_amount', 'fieldtype': 'Currency', 'width': 110,
				'options': 'Company:company:default_currency'},
			{'label': _("Services Total"), 'fieldname': 'service_sales_amount', 'fieldtype': 'Currency', 'width': 110,
				'options': 'Company:company:default_currency'},
			{'label': _("Total Sales"), 'fieldname': 'total_sales_amount', 'fieldtype': 'Currency', 'width': 110,
				'options': 'Company:company:default_currency'},

			{'label': _("Item Code"), 'fieldname': 'applies_to_item', 'fieldtype': 'Link', 'options': 'Item', 'width': 120},
			{'label': _("Item Name"), 'fieldname': 'applies_to_item_name', 'fieldtype': 'Data', 'width': 150},
		]

		return columns


def execute(filters=None):
	return ProjectSalesSummaryReport(filters).run()
