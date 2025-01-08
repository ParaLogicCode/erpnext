# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _, scrub, unscrub
from frappe.utils import getdate, nowdate, flt, cint, cstr
from frappe.desk.query_report import group_report_data
from erpnext import get_default_currency
from erpnext.accounts.report.financial_statements import get_cost_centers_with_children


def execute(filters=None):
	return SalesPurchaseDetailsReport(filters).run()


class SalesPurchaseDetailsReport(object):
	def __init__(self, filters=None, doctype=None):
		self.filters = frappe._dict(filters or {})

		if doctype:
			self.filters.doctype = doctype
		if not self.filters.doctype:
			frappe.throw(_("DocType not provided"))

		self.doc_meta = frappe.get_meta(self.filters.doctype)
		self.item_meta = frappe.get_meta(self.filters.doctype + " Item")

	def run(self):
		self.validate_filters()
		self.set_party_type()
		self.set_company_currency()
		self.set_show_names()
		self.set_fieldnames()

		self.get_entries()
		self.get_itemised_taxes()

		self.prepare_data()
		self.postprocess_data()

		data = self.get_grouped_data()
		columns = self.get_columns()

		skip_total_row = len(self.group_by) > 1

		return columns, data, None, None, None, skip_total_row

	def validate_filters(self):
		self.filters.from_date = getdate(self.filters.from_date or nowdate())
		self.filters.to_date = getdate(self.filters.to_date or nowdate())

		if self.filters.from_date > self.filters.to_date:
			frappe.throw(_("From Date must be before To Date"))

		if self.filters.cost_center and not self.item_meta.has_field("cost_center") and not self.doc_meta.has_field("cost_center"):
			frappe.throw(_("Cannot filter {0} by Cost Center").format(self.filters.doctype))

		if self.filters.qty_only:
			self.filters.show_basic_values = 0
			self.filters.show_discount_values = 0
			self.filters.show_tax_exclusive_values = 0
			self.filters.include_taxes = 0

		if self.filters.group_same_items or not self.item_meta.has_field("packing_slip"):
			self.filters.show_packing_slip = 0

	def set_party_type(self):
		if self.filters.doctype in ("Sales Order", "Delivery Note", "Sales Invoice"):
			self.filters.party_type = "Customer"
		elif self.filters.doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
			self.filters.party_type = "Supplier"
		else:
			frappe.throw(_("Party Type not defined for {0}").format(self.filters.doctype))

	def set_company_currency(self):
		if self.filters.get('company'):
			self.company_currency = frappe.get_cached_value('Company', self.filters.get("company"), "default_currency")
		else:
			self.company_currency = get_default_currency()

	def set_show_names(self):
		self.show_item_name = frappe.defaults.get_global_default('item_naming_by') != "Item Name"

		self.show_party_name = False
		if self.filters.party_type == "Customer":
			if frappe.defaults.get_global_default('cust_master_name') == "Naming Series":
				self.show_party_name = True

		if self.filters.party_type == "Supplier":
			if frappe.defaults.get_global_default('supp_master_name') == "Naming Series":
				self.show_party_name = True

	def set_fieldnames(self):
		self.date_field = "posting_date" if self.doc_meta.has_field("posting_date") else "transaction_date"

		self.party_field = scrub(self.filters.party_type)
		self.party_name_field = self.party_field + "_name"

		filter_to_qty_field = {
			"Stock Qty": "stock_qty",
			"Contents Qty": "alt_uom_qty",
			"Transaction Qty": "qty"
		}
		self.select_qty_field = filter_to_qty_field.get(self.filters.qty_field) or "stock_qty"

		self.qty_fields = ["qty"]
		self.amount_fields = []
		self.rate_fields = []

		if not self.filters.qty_only:
			if cint(self.filters.show_basic_values):
				if cint(self.filters.show_discount_values):
					self.amount_fields += ['base_amount_before_discount', 'base_total_discount']
					self.rate_fields += [
						('discount_percentage', 'base_total_discount', 'base_amount_before_discount', 100),
						('base_price_list_rate', 'base_amount_before_discount')
					]
				self.amount_fields += ['base_amount']
				self.rate_fields += [('base_rate', 'base_amount')]

			if cint(self.filters.show_tax_exclusive_values):
				if cint(self.filters.show_discount_values):
					self.amount_fields += ['base_tax_exclusive_amount_before_discount', 'base_tax_exclusive_total_discount']
				self.amount_fields += ['base_tax_exclusive_amount']
				self.rate_fields += [('base_tax_exclusive_rate', 'base_tax_exclusive_amount')]

			self.amount_fields += ['base_net_amount']
			self.rate_fields += [('base_net_rate', 'base_net_amount')]

	def get_entries(self):
		select_fields, joins = self.get_select_fields_and_joins()
		order_by = self.get_order_by()
		conditions = self.get_conditions()

		select_fields_str = ", ".join(select_fields)
		joins_str = " ".join(joins)
		conditions_str = "and {0}".format(" and ".join(conditions)) if conditions else ""

		self.entries = frappe.db.sql(f"""
			select {select_fields_str}
			from `tab{self.filters.doctype} Item` i
			inner join `tab{self.filters.doctype}` s on i.parent = s.name
			{joins_str}
			where s.docstatus = 1 and {self.date_field} between %(from_date)s and %(to_date)s
				{conditions_str}
			group by s.name, i.name
			order by {order_by}
		""", self.filters, as_dict=1)

	def get_select_fields_and_joins(self):
		select_fields = [
			"s.name as parent", "i.name",
			"s.branch",
			f"{self.date_field} as date",
			f"s.{self.party_field} as party",
			f"s.{self.party_name_field} as party_name",
			"i.item_code", "i.item_name",
			f"i.{self.select_qty_field} as qty",
			"i.uom", "i.stock_uom", "i.alt_uom",
			"im.brand", "im.item_group",
			"s.applies_to_item", "s.applies_to_variant_of",
		]
		joins = ["left join `tabItem` im on im.name = i.item_code"]

		# Rate and Amount
		if not self.filters.qty_only:
			select_fields += [f"i.{f}" for f in self.amount_fields]
			select_fields += [f"i.{r[0]}" for r in self.rate_fields]
			select_fields += ["i.item_tax_detail", "i.item_tax_rate"]

		# Party
		if self.filters.party_type == "Customer":
			joins.append("inner join `tabCustomer` cus on cus.name = s.customer")
			select_fields += ["cus.customer_group as party_group", "'Customer Group' as party_group_dt"]
		elif self.filters.party_type == "Supplier":
			joins.append("inner join `tabSupplier` sup on sup.name = s.supplier")
			select_fields += ["sup.supplier_group as party_group", "'Supplier Group' as party_group_dt"]

		if self.filters.party_type == "Customer":
			select_fields.append("s.territory")

		# Sales Person
		if self.filters.party_type == "Customer":
			joins.append("left join `tabSales Team` sp on sp.parent = s.name and sp.parenttype = %(doctype)s")
			select_fields.append("GROUP_CONCAT(DISTINCT sp.sales_person SEPARATOR ', ') as sales_person")
			select_fields.append("sum(ifnull(sp.allocated_percentage, 100)) as allocated_percentage")

		# Project
		if self.item_meta.has_field("project"):
			select_fields.append("i.project as item_project")
		if self.doc_meta.has_field("project"):
			select_fields.append("s.project as parent_project")

		# Cost Center
		if self.item_meta.has_field("cost_center"):
			select_fields.append("i.cost_center as item_cost_center")
		if self.doc_meta.has_field("cost_center"):
			select_fields.append("s.cost_center as parent_cost_center")

		# Purchase Bill No
		if self.doc_meta.has_field('bill_no'):
			select_fields.append("s.bill_no")
		if self.doc_meta.has_field('bill_date'):
			select_fields.append("s.bill_date")

		# Invoice Related
		if self.filters.doctype == "Sales Invoice":
			select_fields.append("s.stin")

		# Packing Slip
		if self.filters.show_packing_slip:
			select_fields.append("i.packing_slip")

		return select_fields, joins

	def get_order_by(self):
		return f"s.{self.date_field}, s.{self.party_field}, s.name, i.item_code"

	def get_conditions(self):
		conditions = []

		if self.filters.get("company"):
			conditions.append("s.company = %(company)s")

		if self.filters.get("branch"):
			conditions.append("s.branch = %(branch)s")

		if self.filters.get("customer"):
			conditions.append("s.customer = %(customer)s")

		if self.filters.get("customer_group"):
			lft, rgt = frappe.db.get_value("Customer Group", self.filters.customer_group, ["lft", "rgt"])
			conditions.append("""cus.customer_group in (select name from `tabCustomer Group`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.filters.get("supplier"):
			conditions.append("s.supplier = %(supplier)s")

		if self.filters.get("supplier_group"):
			lft, rgt = frappe.db.get_value("Supplier Group", self.filters.supplier_group, ["lft", "rgt"])
			conditions.append("""sup.supplier_group in (select name from `tabSupplier Group`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.filters.get("item_code"):
			is_template = frappe.db.get_value("Item", self.filters.get('item_code'), 'has_variants')
			if is_template:
				conditions.append("im.variant_of = %(item_code)s")
			else:
				conditions.append("im.name = %(item_code)s")

		if self.filters.get("applies_to_item"):
			is_template = frappe.db.get_value("Item", self.filters.get('applies_to_item'), 'has_variants')
			if is_template:
				self.filters.applies_to_items = [self.filters.applies_to_item]
				self.filters.applies_to_items += [d.name for d in frappe.get_all("Item", {'variant_of': self.filters.applies_to_item})]
				conditions.append("s.applies_to_item in %(applies_to_items)s")
			else:
				conditions.append("s.applies_to_item = %(applies_to_item)s")

		if self.filters.get("item_group"):
			lft, rgt = frappe.db.get_value("Item Group", self.filters.item_group, ["lft", "rgt"])
			conditions.append("""im.item_group in (select name from `tabItem Group`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.filters.get("brand"):
			conditions.append("im.brand = %(brand)s")

		if self.filters.get("item_source"):
			conditions.append("im.item_source = %(item_source)s")

		if self.filters.get("territory"):
			lft, rgt = frappe.db.get_value("Territory", self.filters.territory, ["lft", "rgt"])
			conditions.append("""s.territory in (select name from `tabTerritory`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.filters.get("sales_person"):
			lft, rgt = frappe.db.get_value("Sales Person", self.filters.sales_person, ["lft", "rgt"])
			conditions.append("""sp.sales_person in (select name from `tabSales Person`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.filters.get("order_type"):
			conditions.append("s.order_type = %(order_type)s")

		if self.filters.get("transaction_type"):
			conditions.append("s.transaction_type = %(transaction_type)s")

		if self.filters.get("cost_center"):
			self.filters.cost_center = get_cost_centers_with_children(self.filters.get("cost_center"))

			if self.item_meta.has_field("cost_center") and self.doc_meta.has_field("cost_center"):
				conditions.append("IF(s.cost_center IS NULL or s.cost_center = '', i.cost_center, s.cost_center) in %(cost_center)s")
			elif self.item_meta.has_field("cost_center"):
				conditions.append("i.cost_center in %(cost_center)s")
			elif self.doc_meta.has_field("cost_center"):
				conditions.append("s.cost_center in %(cost_center)s")

		if self.filters.get("project"):
			if isinstance(self.filters.project, str):
				self.filters.project = cstr(self.filters.get("project")).strip()
				self.filters.project = [d.strip() for d in self.filters.project.split(',') if d]

			if self.item_meta.has_field("project") and self.doc_meta.has_field("project"):
				conditions.append("IF(i.project IS NULL or i.project = '', s.project, i.project) in %(project)s")
			elif self.item_meta.has_field("project"):
				conditions.append("i.project in %(project)s")
			elif self.doc_meta.has_field("project"):
				conditions.append("s.project in %(project)s")

		if self.filters.get("warehouse"):
			lft, rgt = frappe.db.get_value("Warehouse", self.filters.warehouse, ["lft", "rgt"])
			conditions.append("""i.warehouse in (select name from `tabWarehouse`
				where lft >= {0} and rgt <= {1})""".format(lft, rgt))

		if self.doc_meta.has_field("is_opening"):
			conditions.append("s.is_opening != 'Yes'")

		return conditions

	def get_itemised_taxes(self):
		if self.entries and not self.filters.qty_only:
			tax_doctype = "Sales Taxes and Charges" if self.filters.party_type == "Customer" else "Purchase Taxes and Charges"
			self.itemised_tax, self.tax_columns = get_itemised_taxes(self.entries, tax_doctype)
			self.tax_amount_fields = ["tax_" + scrub(tax) for tax in self.tax_columns]
			self.tax_rate_fields = ["tax_" + scrub(tax) + "_rate" for tax in self.tax_columns]
		else:
			self.itemised_tax, self.tax_columns = {}, []
			self.tax_amount_fields, self.tax_rate_fields = [], []

	def prepare_data(self):
		for d in self.entries:
			# Set UOM based on qty field
			if self.filters.qty_field == "Transaction Qty":
				d.uom = d.uom
			elif self.filters.qty_field == "Contents Qty":
				d.uom = d.alt_uom or d.stock_uom
			else:
				d.uom = d.stock_uom

			# Add additional fields
			d.update({
				"reference_type": "Item",
				"reference": d.item_code,
				"group_doctype": "Item Group",
				"group": d.item_group,
				"brand": d.brand,
				"cost_center": d.parent_cost_center or d.item_cost_center,
				"project": d.item_project or d.parent_project,
				self.party_name_field: d.party_name,
				"party_name": d.party_name,
				"disable_item_formatter": cint(self.show_item_name),
				"disable_party_name_formatter": cint(self.show_party_name),
				"applies_to_variant_of": d.applies_to_variant_of or d.applies_to_item,
			})

			if "Group by Item" in (self.filters.group_by_1, self.filters.group_by_2, self.filters.group_by_3):
				d['reference_type'] = self.filters.doctype
				d['reference'] = d.get("parent")

			# Add tax fields
			for f, tax in zip(self.tax_amount_fields, self.tax_columns):
				tax_amount = self.itemised_tax.get(d.name, {}).get(tax, {}).get("tax_amount", 0.0)
				d[f] = flt(tax_amount)
			for f, tax in zip(self.tax_rate_fields, self.tax_columns):
				tax_rate = self.itemised_tax.get(d.name, {}).get(tax, {}).get("tax_rate", 0.0)
				d[f] = flt(tax_rate)

			# Has
			if d.get("branch"):
				self.filters.has_branch = True
			if d.get("project"):
				self.filters.has_project = True

	def postprocess_data(self):
		for d in self.entries:
			self.postprocess_row(d)
			self.apply_sales_person_contribution(d)

	def get_grouped_data(self):
		data = self.entries

		self.group_by = [None]
		for i in range(3):
			group_label = self.filters.get("group_by_" + str(i + 1), "").replace("Group by ", "")

			if not group_label:
				continue
			if group_label in ("Customer", "Supplier"):
				group_field = "party"
			elif group_label == "Transaction":
				group_field = "parent"
			elif group_label == "Item":
				group_field = "item_code"
			elif group_label in ("Customer Group", "Supplier Group"):
				group_field = "party_group"
			else:
				group_field = scrub(group_label)

			self.group_by.append(group_field)

		# Group same items
		if cint(self.filters.get("group_same_items")) and not self.filters.totals_only:
			data = group_report_data(
				data,
				("item_code", "item_name", "uom", "parent"),
				calculate_totals=self.calculate_group_totals,
				totals_only=True
			)

		if len(self.group_by) <= 1:
			return data

		return group_report_data(
			data,
			self.group_by,
			calculate_totals=self.calculate_group_totals,
			totals_only=self.filters.totals_only
		)

	def calculate_group_totals(self, data, group_field, group_value, grouped_by):
		total_fields = self.qty_fields + self.amount_fields + self.tax_amount_fields
		if self.filters.sales_person:
			total_fields.append('actual_net_amount')

		averageif_fields = self.tax_rate_fields

		totals = frappe._dict({
			"disable_item_formatter": cint(self.show_item_name),
			"disable_party_name_formatter": cint(self.show_party_name)
		})

		# Copy grouped by into total row
		for f, g in grouped_by.items():
			totals[f] = g

		# Set zeros
		for f in total_fields + averageif_fields + [f + "_count" for f in averageif_fields]:
			totals[f] = 0

		# Add totals
		uoms = set()
		for d in data:
			uoms.add(d.uom)

			for f in total_fields:
				totals[f] += flt(d.get(f))

			for f in averageif_fields:
				if flt(d[f]):
					totals[f] += flt(d.get(f))
					totals[f + "_count"] += 1

		if len(uoms) == 1:
			totals.uom = list(uoms)[0]

		# Set group values
		if data:
			if group_field == ("item_code", "item_name", "uom", "parent"):
				for f, v in data[0].items():
					if f not in totals:
						totals[f] = v

			if 'parent' in grouped_by:
				fields_to_copy = [
					'date', 'party', 'party_name', 'sales_person', 'territory',
					'stin', 'bill_no', 'bill_date', 'branch',
				]
				for f in fields_to_copy:
					if f in data[0]:
						totals[f] = data[0][f]

				projects = set([d.get('project') for d in data if d.get('project')])
				totals['project'] = ", ".join(projects)

			if 'item_code' in grouped_by:
				totals['item_name'] = data[0].get('item_name')
				totals['group_doctype'] = "Item Group"
				totals['group'] = data[0].get('item_group')
				totals['brand'] = data[0].get('brand')

			if group_field in ('applies_to_item', 'applies_to_variant_of'):
				totals['item_name'] = frappe.db.get_value("Item", group_value, 'item_name', cache=1) if group_value else None
				totals['group_doctype'] = "Item Group"
				totals['group'] = frappe.db.get_value("Item", group_value, 'item_group', cache=1) if group_value else None
				totals['brand'] = frappe.db.get_value("Item", group_value, 'brand', cache=1) if group_value else None

			if group_field == 'party':
				totals['group_doctype'] = data[0].get("party_group_dt")
				totals['group'] = data[0].get("party_group")
				totals['party_name'] = data[0].get("party_name")
				totals['party_type'] = self.filters.party_type

		# Set reference field
		if group_field == ("item_code", "item_nane", "uom", "parent") and data:
			totals['reference_type'] = data[0].get('reference_type')
			totals['reference'] = data[0].get('reference')
		else:
			reference_field = group_field[0] if isinstance(group_field, (list, tuple)) else group_field
			reference_dt = self.fieldname_to_doctype(reference_field)
			totals['reference_type'] = reference_dt
			totals['reference'] = grouped_by.get(reference_field) if group_field else "Total"

			if not group_field and self.group_by == [None]:
				totals['parent'] = "'Total'"

		# Calculate sales person contribution percentage
		if totals.get('actual_net_amount'):
			totals['allocated_percentage'] = totals['base_net_amount'] / totals['actual_net_amount'] * 100

		self.postprocess_row(totals)
		return totals

	def fieldname_to_doctype(self, fieldname):
		group_reference_doctypes = {
			"party": self.filters.party_type,
			"parent": self.filters.doctype,
			"item_code": "Item",
			"applies_to_item": "Item",
			"applies_to_variant_of": "Item",
			"party_group": "Customer Group" if self.filters.party_type == "Customer" else "Supplier Group"
		}

		return group_reference_doctypes.get(fieldname, unscrub(cstr(fieldname)))

	def postprocess_row(self, row):
		# Calculate rate
		for d in self.rate_fields:
			divisor_field = "qty"
			multiplier = 1

			if len(d) == 2:
				target, source = d
			elif len(d) == 3:
				target, source, divisor_field = d
			else:
				target, source, divisor_field, multiplier = d

			if flt(row[divisor_field]):
				row[target] = flt(row[source]) / flt(row[divisor_field]) * flt(multiplier)
			else:
				row[target] = flt(row.get(target)) or 0

		# Calculate total taxes and grand total
		if not self.filters.qty_only:
			row["total_tax_amount"] = 0.0
			for f in self.tax_amount_fields:
				row["total_tax_amount"] += row[f]

			row["grand_total"] = row["base_net_amount"] + row["total_tax_amount"]

		# Calculate tax rates by averaging
		for f in self.tax_rate_fields:
			row[f] = row.get(f, 0.0)
			if flt(row.get(f + "_count")):
				row[f] /= flt(row.get(f + "_count"))

			if f + "_count" in row:
				del row[f + "_count"]

	def apply_sales_person_contribution(self, row):
		if self.filters.sales_person:
			fields = self.qty_fields.copy()

			if not self.filters.qty_only:
				row['actual_net_amount'] = row["base_net_amount"]
				fields += ['total_tax_amount', 'grand_total'] + self.amount_fields + self.tax_amount_fields

			for f in fields:
				row[f] *= row.allocated_percentage / 100

	def get_columns(self):
		columns = [
			{
				"label": _("Reference"),
				"fieldname": "reference",
				"fieldtype": "Dynamic Link",
				"options": "reference_type",
				"width": 220,
				"column_filter": "has_group",
			},
			# {
			# 	"label": _("Type"),
			# 	"fieldname": "reference_type",
			# 	"fieldtype": "Data",
			# 	"width": 90,
			# 	"column_filter": "has_group",
			# },
			{
				"label": _("Date"),
				"fieldname": "date",
				"fieldtype": "Date",
				"width": 80
			},
			{
				"label": _(self.filters.doctype),
				"fieldname": "parent",
				"fieldtype": "Link",
				"options": self.filters.doctype,
				"width": 100
			},
			{
				"label": _("Tax Inv #"),
				"fieldname": "stin",
				"fieldtype": "Int",
				"width": 60
			},
			{
				"label": _("Bill No"),
				"fieldname": "bill_no",
				"fieldtype": "Data",
				"width": 80
			},
			{
				"label": _("Bill Date"),
				"fieldname": "bill_date",
				"fieldtype": "Date",
				"width": 80
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
				"label": _("% Contribution"),
				"fieldname": "allocated_percentage",
				"fieldtype": "Percent",
				"width": 60
			},
			{
				"label": _("Qty"),
				"fieldname": "qty",
				"fieldtype": "Float",
				"width": 80
			},
			{
				"label": _("UOM"),
				"fieldname": "uom",
				"fieldtype": "Link",
				"options": "UOM",
				"width": 50
			},
		]

		value_columns = [
			{
				"label": _("Rate Before Discount"),
				"fieldname": "base_price_list_rate",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Amount Before Discount"),
				"fieldname": "base_amount_before_discount",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Total Discount"),
				"fieldname": "base_total_discount",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Discount (%)"),
				"fieldname": "discount_percentage",
				"fieldtype": "Percent",
				"width": 60
			},
			{
				"label": _("Rate"),
				"fieldname": "base_rate",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Amount"),
				"fieldname": "base_amount",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Rate Before Discount (Tax Exclusive)"),
				"fieldname": "base_tax_exclusive_price_list_rate",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Amount Before Discount (Tax Exclusive)"),
				"fieldname": "base_tax_exclusive_amount_before_discount",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Total Discount (Tax Exclusive)"),
				"fieldname": "base_tax_exclusive_total_discount",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Rate (Tax Exclusive)"),
				"fieldname": "base_tax_exclusive_rate",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Amount (Tax Exclusive)"),
				"fieldname": "base_tax_exclusive_amount",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Net Rate"),
				"fieldname": "base_net_rate",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
			{
				"label": _("Net Amount"),
				"fieldname": "base_net_amount",
				"fieldtype": "Currency",
				"options": "Company:company:default_currency",
				"width": 110
			},
		]

		included_amount_fields = self.amount_fields + [d[0] for d in self.rate_fields]
		for c in value_columns:
			if c['fieldname'] in included_amount_fields:
				columns.append(c)

		if not self.filters.qty_only:
			columns += [
				{
					"label": _("Taxes and Charges"),
					"fieldname": "total_tax_amount",
					"fieldtype": "Currency",
					"options": "Company:company:default_currency",
					"width": 110
				},
				{
					"label": _("Grand Total"),
					"fieldname": "grand_total",
					"fieldtype": "Currency",
					"options": "Company:company:default_currency",
					"width": 110
				},
			]

		if self.filters.include_taxes:
			for tax_description in self.tax_columns:
				amount_field = "tax_" + scrub(tax_description)
				rate_field = amount_field + "_rate"
				columns += [
					{
						"label": _(tax_description) + " (%)",
						"fieldname": rate_field,
						"fieldtype": "Percent",
						"width": 60
					},
					{
						"label": _(tax_description),
						"fieldname": amount_field,
						"fieldtype": "Currency",
						"options": "Company:company:default_currency",
						"width": 110
					},
				]

		columns += [
			{
				"label": _("Package"),
				"fieldname": "packing_slip",
				"fieldtype": "Link",
				"options": "Packing Slip",
				"width": 80
			},
			{
				"label": _("Sales Person"),
				"fieldname": "sales_person",
				"fieldtype": "Data",
				"width": 150
			},
			{
				"label": _("Branch"),
				"fieldname": "branch",
				"fieldtype": "Link",
				"options": "Branch",
				"width": 100
			},
			{
				"label": _("Project"),
				"fieldname": "project",
				"fieldtype": "Link",
				"options": "Project",
				"width": 100
			},
			{
				"label": _("Territory"),
				"fieldname": "territory",
				"fieldtype": "Link",
				"options": "Territory",
				"width": 100
			},
			{
				"label": _("Cost Center"),
				"fieldname": "cost_center",
				"fieldtype": "Link",
				"options": "Cost Center",
				"width": 100
			},
			{
				"label": _("Group"),
				"fieldname": "group",
				"fieldtype": "Dynamic Link",
				"options": "group_doctype",
				"width": 100
			},
			{
				"label": _("Brand"),
				"fieldname": "brand",
				"fieldtype": "Link",
				"options": "Brand",
				"width": 100
			},
		]

		if len(self.group_by) <= 1:
			columns = [c for c in columns if c.get('fieldname') not in ('reference_type', 'reference')]

		if not self.show_item_name:
			columns = [c for c in columns if c.get('fieldname') != 'item_name']
		if not self.show_party_name:
			columns = [c for c in columns if c.get('fieldname') != 'party_name']

		if not self.doc_meta.has_field("bill_no"):
			columns = [c for c in columns if c.get('fieldname') != 'bill_no']
		if not self.doc_meta.has_field("bill_date"):
			columns = [c for c in columns if c.get('fieldname') != 'bill_date']

		if not self.item_meta.has_field("cost_center"):
			columns = [c for c in columns if c.get('fieldname') != 'cost_center']
		if not self.item_meta.has_field("project") and not self.doc_meta.has_field("project"):
			columns = [c for c in columns if c.get('fieldname') != 'project']

		if not self.filters.has_project:
			columns = [c for c in columns if c.get('fieldname') != 'project']

		if self.filters.party_type != "Customer":
			columns = [c for c in columns if c.get('fieldname') not in ('sales_person', 'territory')]

		if not self.filters.sales_person:
			columns = [c for c in columns if c.get('fieldname') != 'allocated_percentage']

		if self.filters.doctype != "Sales Invoice":
			columns = [c for c in columns if c.get('fieldname') != 'stin']

		if not self.filters.show_packing_slip:
			columns = [c for c in columns if c.get('fieldname') != 'packing_slip']

		if not self.filters.has_branch:
			columns = [c for c in columns if c.get('fieldname') != 'branch']

		if self.filters.totals_only:
			if "item_code" not in self.group_by:
				columns = [c for c in columns if c.get('fieldname') not in ('item_code', 'item_name')]

			if "party" not in self.group_by and "parent" not in self.group_by:
				columns = [c for c in columns if c.get('fieldname') not in ('party', 'party_name')]

			if "parent" not in self.group_by:
				columns = [c for c in columns if c.get('fieldname') not in (
					'parent', 'date', 'sales_person', 'territory', 'stin',
				)]

			if "project" not in self.group_by:
				columns = [c for c in columns if c.get('fieldname') != "project"]
			if "cost_center" not in self.group_by:
				columns = [c for c in columns if c.get('fieldname') != "cost_center"]
			if "branch" not in self.group_by:
				columns = [c for c in columns if c.get('fieldname') != "branch"]

		return columns


def get_itemised_taxes(entries, tax_doctype, get_description_as_tax_head=True):
	import json

	parent_names = []
	for item in entries:
		if item.parent:
			parent_names.append(item.parent)

	parent_names = list(set(parent_names))
	if not parent_names:
		return {}, []

	tax_row_data = frappe.db.sql(f"""
		select name, account_head, description, charge_type, rate
		from `tab{tax_doctype}`
		where parent in %s
	""", [parent_names], as_dict=1)

	tax_row_map = {}
	for tax in tax_row_data:
		tax_row_map[tax.name] = tax

	itemised_taxes = {}
	tax_heads = set()

	for item in entries:
		item_tax_detail = json.loads(item.item_tax_detail) if item.item_tax_detail else {}
		item_tax_rate = json.loads(item.item_tax_rate) if item.item_tax_rate else {}

		for tax_row_name, tax_amount in item_tax_detail.items():
			if not tax_amount:
				continue

			tax_row = tax_row_map.get(tax_row_name, {})
			if not tax_row:
				continue

			tax_head = tax_row.description if get_description_as_tax_head else tax_row.account_head
			tax_heads.add(tax_head)

			item_tax_dict = itemised_taxes.setdefault(item.name, {}).setdefault(tax_head, {"tax_amount": 0, "tax_rate": 0})
			item_tax_dict["tax_amount"] += tax_amount

			tax_rate = flt(item_tax_rate.get(tax_row.account_head) or tax_row.rate) if tax_row.charge_type != "Actual" else 0
			item_tax_dict["tax_rate"] = tax_rate

	tax_heads = sorted(list(tax_heads))

	return itemised_taxes, tax_heads
