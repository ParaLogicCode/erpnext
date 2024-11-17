# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _, scrub
from frappe.utils import cint, cstr, getdate


def execute(filters=None):
	return ItemsToBeBilled(filters).run("Customer")


class ItemsToBeBilled:
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or dict())
		if self.filters.from_date and self.filters.to_date and self.filters.from_date > self.filters.to_date:
			frappe.throw(_("Date Range is incorrect"))

	def run(self, party_type):
		self.filters.party_type = party_type

		self.show_item_name = frappe.defaults.get_global_default('item_naming_by') != "Item Name"

		self.show_party_name = False
		if party_type == "Customer":
			self.show_party_name = frappe.defaults.get_global_default('cust_master_name') == "Naming Series"
		if party_type == "Supplier":
			self.show_party_name = frappe.defaults.get_global_default('supp_master_name') == "Naming Series"

		self.order_doctype = "Sales Order" if self.filters.party_type == "Customer" else "Purchase Order"
		self.delivery_doctype = "Delivery Note" if self.filters.party_type == "Customer" else "Purchase Receipt"

		self.get_data()
		self.prepare_data()

		columns = self.get_columns()
		return columns, self.data

	def get_data(self):
		order_data = []
		if not self.filters.doctype or self.filters.doctype == self.order_doctype:
			select_fields, joins = self.get_select_fields_and_joins(self.order_doctype)
			select_fields_str = ", ".join(select_fields)
			joins_str = " ".join(joins)

			conditions = self.get_conditions(self.order_doctype)
			conditions_str = "AND {}".format(" AND ".join(conditions)) if conditions else ""

			if self.order_doctype == "Sales Order":
				skip_delivery_condition = " AND i.skip_delivery_note = 1"
			else:
				skip_delivery_condition = " AND im.is_stock_item = 0 AND im.is_fixed_asset = 0"

			order_data = frappe.db.sql(f"""
				SELECT '{self.order_doctype}' as doctype, o.transaction_date, {select_fields_str}
				FROM `tab{self.order_doctype}` o
				INNER JOIN `tab{self.order_doctype} Item` i ON i.parent = o.name
				INNER JOIN `tabItem` im on im.name = i.item_code
				{joins_str}
				WHERE
					o.docstatus = 1 AND o.status != 'Closed'
					AND (i.billed_qty + i.returned_qty) < i.qty
					{conditions_str} {skip_delivery_condition}
				GROUP BY o.name, i.name
			""", self.filters, as_dict=1)

		delivery_data = []
		if not self.filters.doctype or self.filters.doctype == self.delivery_doctype:
			select_fields, joins = self.get_select_fields_and_joins(self.delivery_doctype)
			select_fields_str = ", ".join(select_fields)
			joins_str = " ".join(joins)

			conditions = self.get_conditions(self.delivery_doctype)
			conditions_str = "AND {}".format(" AND ".join(conditions)) if conditions else ""

			order_reference_field = scrub(self.order_doctype)

			delivery_data = frappe.db.sql(f"""
				SELECT '{self.delivery_doctype}' as doctype, o.posting_date as transaction_date, {select_fields_str}
				FROM `tab{self.delivery_doctype}` o
				INNER JOIN `tab{self.delivery_doctype} Item` i ON i.parent = o.name
				INNER JOIN `tabItem` im on im.name = i.item_code
				{joins_str}
				WHERE
					o.docstatus = 1 AND o.status != 'Closed'
					AND (i.billed_qty + i.returned_qty) < i.qty
					AND (
						im.is_stock_item = 1
						OR im.is_fixed_asset = 1
						OR (i.{order_reference_field} = '' or i.{order_reference_field} is null)
					)
					{conditions_str}
				GROUP BY o.name, i.name
			""", self.filters, as_dict=1)

		self.data = order_data + delivery_data
		self.sort_data()

	def sort_data(self):
		self.data = sorted(self.data, key=lambda d: (getdate(d.transaction_date), d.creation))

	def get_select_fields_and_joins(self, doctype):
		fieldnames = self.get_fieldnames()

		select_fields = [
			"o.name", "o.company", "o.creation", "o.currency", "o.project",
			f"o.{fieldnames.party} as party", f"o.{fieldnames.party_name} as party_name",
			"i.item_code", "i.item_name", "i.warehouse", "i.name as row_name",
			f"i.{fieldnames.qty} as qty", "i.uom", "i.stock_uom", "i.alt_uom",
			"i.conversion_factor", "i.alt_uom_size",
			"i.billed_qty", "i.returned_qty", "i.billed_amt",
			"i.rate", "i.amount", "im.item_group", "im.brand",
			"i.discount_percentage", "i.amount_before_discount"
		]

		joins = []

		if self.filters.party_type == "Customer":
			joins += [
				"inner join `tabCustomer` cus on cus.name = o.customer",
				f"left join `tabSales Team` sp on sp.parent = o.name and sp.parenttype = '{doctype}'",
			]
			select_fields.append("GROUP_CONCAT(DISTINCT sp.sales_person SEPARATOR ', ') as sales_person")
		elif self.filters.party_type == "Supplier":
			joins.append("inner join `tabSupplier` sup on sup.name = o.supplier")

		return select_fields, joins

	def get_fieldnames(self):
		fields = frappe._dict({})

		fields.party = scrub(self.filters.party_type)
		fields.party_name = fields.party + "_name"

		qty_field_filters = {
			"Stock Qty": "stock_qty",
			"Contents Qty": "alt_uom_qty",
			"Transaction Qty": "qty"
		}
		fields.qty = qty_field_filters.get(self.filters.qty_field) or "stock_qty"

		return fields

	def get_date_field(self, doctype):
		if doctype in ["Sales Order", "Purchase Order"]:
			return "o.transaction_date"
		else:
			return "o.posting_date"

	def get_conditions(self, doctype):
		conditions = []

		if self.filters.company:
			conditions.append("o.company = %(company)s")

		if self.filters.name:
			conditions.append("o.name = %(name)s")

		if self.filters.transaction_type:
			conditions.append("o.transaction_type = %(transaction_type)s")

		if self.filters.customer:
			conditions.append("o.customer = %(customer)s")

		if self.filters.supplier:
			conditions.append("o.supplier = %(supplier)s")

		date_field = self.get_date_field(doctype)
		if self.filters.from_date:
			conditions.append("{} >= %(from_date)s".format(date_field))
		if self.filters.to_date:
			conditions.append("{} <= %(to_date)s".format(date_field))

		if self.filters.territory:
			lft, rgt = frappe.db.get_value("Territory", self.filters.territory, ["lft", "rgt"])
			conditions.append("""o.territory in (select name from `tabTerritory`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.filters.warehouse:
			lft, rgt = frappe.db.get_value("Warehouse", self.filters.warehouse, ["lft", "rgt"])
			conditions.append("""i.warehouse in (select name from `tabWarehouse`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.filters.project:
			if isinstance(self.filters.project, str):
				self.filters.project = cstr(self.filters.get("project")).strip()
				self.filters.project = [d.strip() for d in self.filters.project.split(',') if d]

			if frappe.get_meta(doctype + " Item").has_field("project") and frappe.get_meta(doctype).has_field("project"):
				conditions.append("IF(i.project IS NULL or i.project = '', o.project, i.project) in %(project)s")
			elif frappe.get_meta(doctype + " Item").has_field("project"):
				conditions.append("i.project in %(project)s")
			elif frappe.get_meta(doctype).has_field("project"):
				conditions.append("o.project in %(project)s")

		if self.filters.brand:
			conditions.append("im.brand = %(brand)s")

		if self.filters.item_source:
			conditions.append("im.item_source = %(item_source)s")

		if self.filters.item_code:
			if frappe.db.get_value("Item", self.filters.item_code, 'has_variants'):
				conditions.append("im.variant_of = %(item_code)s")
			else:
				conditions.append("i.item_code = %(item_code)s")

		if self.filters.item_group:
			lft, rgt = frappe.db.get_value("Item Group", self.filters.item_group, ["lft", "rgt"])
			conditions.append("""im.item_group IN (SELECT name FROM `tabItem Group`
				WHERE lft >= {0} AND rgt <= {1})""".format(lft, rgt))

		if self.filters.customer_group:
			lft, rgt = frappe.db.get_value("Customer Group", self.filters.customer_group, ["lft", "rgt"])
			conditions.append("""cus.customer_group IN (SELECT name FROM `tabCustomer Group`
				WHERE lft >= {0} AND rgt <= {1})""".format(lft, rgt))

		if self.filters.supplier_group:
			lft, rgt = frappe.db.get_value("Supplier Group", self.filters.supplier_group, ["lft", "rgt"])
			conditions.append("""sup.supplier_group IN (SELECT name FROM `tabSupplier Group`
				WHERE lft >= {0} AND rgt <= {1})""".format(lft, rgt))

		if self.filters.sales_person:
			lft, rgt = frappe.db.get_value("Sales Person", self.filters.sales_person, ["lft", "rgt"])
			conditions.append("""sp.sales_person in (select name from `tabSales Person`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if frappe.get_meta(doctype).has_field("skip_sales_invoice"):
			conditions.append("o.skip_sales_invoice = 0")
		if frappe.get_meta(doctype + " Item").has_field("skip_sales_invoice"):
			conditions.append("i.skip_sales_invoice = 0")

		return conditions

	def prepare_data(self):
		self.has_project = False

		for d in self.data:
			# Set UOM based on qty field
			if self.filters.qty_field == "Contents Qty":
				d.uom = d.alt_uom or d.stock_uom
				d.billed_qty = d.billed_qty * d.conversion_factor * d.alt_uom_size
				d.returned_qty = d.returned_qty * d.conversion_factor * d.alt_uom_size
			elif self.filters.qty_field == "Stock Qty":
				d.uom = d.stock_uom
				d.billed_qty = d.billed_qty * d.conversion_factor
				d.returned_qty = d.returned_qty * d.conversion_factor

			if d.get("project"):
				self.has_project = True

			d['rate'] = d['amount'] / d['qty'] if d['qty'] else d['rate']
			d["remaining_qty"] = d["qty"] - d["billed_qty"] - d['returned_qty']

			d["remaining_amt"] = d["amount"] - d["billed_amt"]

			if d["amount"] >= 0:
				d["remaining_amt"] = max(0, d["remaining_amt"])
			else:
				d["remaining_amt"] = min(0, d["remaining_amt"])

			d["delay_days"] = max((getdate() - getdate(d["transaction_date"])).days, 0)

			d["disable_item_formatter"] = cint(self.show_item_name)
			d["disable_party_name_formatter"] = cint(self.show_party_name)

	def get_columns(self):
		columns = [
			{
				"label": _("Date"),
				"fieldname": "transaction_date",
				"fieldtype": "Date",
				"width": 80
			},
			{
				"label": _("Document Type"),
				"fieldname": "doctype",
				"fieldtype": "Data",
				"width": 90 if self.filters.party_type == "Customer" else 110
			},
			{
				"label": _("Document"),
				"fieldname": "name",
				"fieldtype": "Dynamic Link",
				"options": "doctype",
				"width": 120
			},
			{
				"label": _("Project"),
				"fieldname": "project",
				"fieldtype": "Link",
				"options": "Project",
				"width": 100
			},
			{
				"label": _(self.filters.party_type),
				"fieldname": "party",
				"fieldtype": "Link",
				"options": self.filters.party_type,
				"width": 80 if self.show_party_name else 150
			},
			{
				"label": _(self.filters.party_type) + " Name",
				"fieldname": "party_name",
				"fieldtype": "Data",
				"width": 150
			},
			{
				"label": _("Item Code"),
				"fieldname": "item_code",
				"fieldtype": "Link",
				"options": "Item",
				"width": 100 if self.show_item_name else 150
			},
			{
				"label": _("Item Name"),
				"fieldname": "item_name",
				"fieldtype": "Data",
				"width": 150
			},
			{
				"label": _("UOM"),
				"fieldtype": "Link",
				"options": "UOM",
				"fieldname": "uom",
				"width": 50
			},
			{
				"label": _("Remaining"),
				"fieldname": "remaining_qty",
				"fieldtype": "Float",
				"width": 80
			},
			{
				"label": _("Remaining Amount"),
				"fieldname": "remaining_amt",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 120
			},
			{
				"label": _("Qty"),
				"fieldname": "qty",
				"fieldtype": "Float",
				"width": 80
			},
			{
				"label": _("Billed"),
				"fieldname": "billed_qty",
				"fieldtype": "Float",
				"width": 80
			},
			{
				"label": _("Returned"),
				"fieldname": "returned_qty",
				"fieldtype": "Float",
				"width": 80
			},
			{
				"label": _("Amount"),
				"fieldname": "amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 120
			},
			{
				"label": _("Billed Amount"),
				"fieldname": "billed_amt",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 120
			},
			{
				"label": _("Sales Person"),
				"fieldtype": "Data",
				"fieldname": "sales_person",
				"width": 150
			},
			{
				"label": _("Delay Days"),
				"fieldname": "delay_days",
				"fieldtype": "Int",
				"width": 85
			},
			{
				"label": _("Item Group"),
				"fieldname": "item_group",
				"fieldtype": "Link",
				"options": "Item Group",
				"width": 90
			},
			{
				"label": _("Brand"),
				"fieldname": "brand",
				"fieldtype": "Link",
				"options": "Brand",
				"width": 60
			},
			{
				"label": _("Warehouse"),
				"fieldname": "warehouse",
				"fieldtype": "Link",
				"options": "Warehouse",
				"width": 90
			},
		]

		if not self.show_item_name:
			columns = [c for c in columns if c['fieldname'] != 'item_name']
		
		if not self.show_party_name:
			columns = [c for c in columns if c['fieldname'] != 'party_name']

		if self.filters.party_type != "Customer":
			columns = [c for c in columns if c['fieldname'] != 'sales_person']

		if not self.has_project:
			columns = [c for c in columns if c['fieldname'] != 'project']

		return columns
