# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
import erpnext
from frappe import _
from frappe.desk.reportview import get_match_cond, get_filters_cond
from frappe.utils import nowdate, getdate, flt, cstr, cint
from collections import defaultdict
from frappe.utils import unique


# searches for active employees
@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def employee_query(doctype, txt, searchfield, start, page_len, filters):
	conditions = []
	fields = get_fields("Employee", ["name", "employee_name"])

	searchfields = frappe.get_meta("Employee").get_search_fields()
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	return frappe.db.sql("""select {fields} from `tabEmployee`
		where status = 'Active'
			and docstatus < 2
			and ({scond})
			{fcond} {mcond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			if(locate(%(_txt)s, employee_name), locate(%(_txt)s, employee_name), 99999),
			modified desc,
			name, employee_name
		limit %(start)s, %(page_len)s""".format(**{
			'fields': ", ".join(fields),
			"scond": searchfields,
			'key': searchfield,
			'fcond': get_filters_cond(doctype, filters, conditions),
			'mcond': get_match_cond(doctype)
		}), {
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace("%", ""),
			'start': start,
			'page_len': page_len
		})


# searches for customer
@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def customer_query(doctype, txt, searchfield, start, page_len, filters):
	conditions = []
	cust_master_name = frappe.defaults.get_user_default("cust_master_name")

	fields = ["name", "customer_name", "reference"]
	fields = get_fields("Customer", fields)

	if cust_master_name == "Customer Name":
		fields.remove("customer_name")

	searchfields = frappe.get_meta("Customer").get_search_fields()
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	return frappe.db.sql("""select {fields} from `tabCustomer`
		where docstatus < 2
			and ({scond}) and disabled=0
			{fcond} {mcond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			if(locate(%(_txt)s, customer_name), locate(%(_txt)s, customer_name), 99999),
			modified desc,
			name, customer_name
		limit %(start)s, %(page_len)s""".format(**{
			"fields": ", ".join(fields),
			"scond": searchfields,
			"mcond": get_match_cond(doctype),
			"fcond": get_filters_cond(doctype, filters, conditions).replace('%', '%%'),
		}), {
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace("%", ""),
			'start': start,
			'page_len': page_len
		})


# searches for supplier
@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def supplier_query(doctype, txt, searchfield, start, page_len, filters):
	supp_master_name = frappe.defaults.get_user_default("supp_master_name")

	if supp_master_name == "Supplier Name":
		fields = ["name", "supplier_group"]
	else:
		fields = ["name", "supplier_name", "supplier_group"]

	fields = get_fields("Supplier", fields)

	return frappe.db.sql("""select {field} from `tabSupplier`
		where docstatus < 2
			and ({key} like %(txt)s
				or supplier_name like %(txt)s) and disabled=0
			{mcond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			if(locate(%(_txt)s, supplier_name), locate(%(_txt)s, supplier_name), 99999),
			modified desc,
			name, supplier_name
		limit %(start)s, %(page_len)s """.format(**{
			'field': ', '.join(fields),
			'key': searchfield,
			'mcond':get_match_cond(doctype)
		}), {
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace("%", ""),
			'start': start,
			'page_len': page_len
		})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def tax_account_query(doctype, txt, searchfield, start, page_len, filters):
	company_currency = erpnext.get_company_currency(filters.get('company'))

	def get_accounts(with_account_type_filter):
		account_type_condition = ''
		if with_account_type_filter:
			account_type_condition = "AND account_type in %(account_types)s"

		accounts = frappe.db.sql("""
			SELECT name, parent_account
			FROM `tabAccount`
			WHERE `tabAccount`.docstatus!=2
				{account_type_condition}
				AND is_group = 0
				AND company = %(company)s
				AND account_currency = %(currency)s
				AND `{searchfield}` LIKE %(txt)s
			ORDER BY idx DESC, name
			LIMIT %(offset)s, %(limit)s
		""".format(account_type_condition=account_type_condition, searchfield=searchfield),
			dict(
				account_types=filters.get("account_type"),
				company=filters.get("company"),
				currency=company_currency,
				txt="%{}%".format(txt),
				offset=start,
				limit=page_len
			)
		)

		return accounts

	tax_accounts = get_accounts(True)

	if not tax_accounts:
		tax_accounts = get_accounts(False)

	return tax_accounts


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def subcontracted_item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	if not filters:
		filters = {}

	filters["is_sub_contracted_item"] = 1

	purchase_order = filters.pop("purchase_order", None)
	if purchase_order:
		po_item_codes = frappe.get_all("Purchase Order Item", {"parent": purchase_order}, pluck="item_code")
		po_item_codes = list(set(po_item_codes))

		if po_item_codes:
			filters["name"] = ("in", po_item_codes)

	return item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=as_dict)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	conditions = []

	# Get searchfields from meta and use in Item Link field query
	meta = frappe.get_meta("Item", cached=True)
	searchfields = meta.get_search_fields()
	if "description" in searchfields:
		searchfields.remove("description")

	# Columns
	columns = ''
	extra_searchfields = [field for field in searchfields
		if not field in ["name", "item_name", "item_group", "brand"]]

	if extra_searchfields:
		columns = ", " + ", ".join(extra_searchfields)

	searchfields = searchfields + [field for field in [searchfield or "name", "item_code", "item_group", "item_name"]
		if not field in searchfields]
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	# Description Conditions
	description_cond = ''
	if frappe.db.count('Item', cache=True) < 50000:
		# scan description only if items are less than 50000
		description_cond = 'or tabItem.description LIKE %(txt)s'

	# Item applicability conditions
	has_applicable_items_cond = ""
	if filters and isinstance(filters, dict) and filters.get('has_applicable_items'):
		filters.pop('has_applicable_items')
		has_applicable_items_cond = """ and exists(select iai.name
			from `tabItem Applicable Item` iai
			where tabItem.name = iai.parent or tabItem.variant_of = iai.parent)
		"""

	# Default Conditions
	default_conditions = []
	default_disabled_condition = "tabItem.disabled = 0"
	default_variant_condition = "tabItem.has_variants = 0"
	default_eol_condition = "(tabItem.end_of_life > %(today)s or ifnull(tabItem.end_of_life, '0000-00-00')='0000-00-00')"

	if filters and isinstance(filters, dict):
		if filters.get('include_disabled'):
			filters.pop('include_disabled')
		else:
			if not filters.get('disabled'):
				default_conditions.append(default_disabled_condition)
			if not filters.get('end_of_life'):
				default_conditions.append(default_eol_condition)

		if filters.get('is_model'):
			filters.pop('is_model')
			default_conditions.append("(tabItem.has_variants = 1 or ifnull(tabItem.variant_of, '') = '')")

		elif filters.get('is_variant'):
			filters.pop('is_variant')
			default_conditions.append(default_variant_condition)
			if filters.get('variant_of'):
				variant_of = filters.pop('variant_of')
				default_conditions.append("(tabItem.variant_of = {0} or tabItem.name = {0})".format(frappe.db.escape(variant_of)))

		elif filters.get('include_templates'):
			filters.pop('include_templates')

		elif 'has_variants' not in filters:
			default_conditions.append(default_variant_condition)
	else:
		default_conditions = [default_disabled_condition, default_variant_condition, default_eol_condition]

	default_conditions = "and {0}".format(" and ".join(default_conditions)) if default_conditions else ""

	return frappe.db.sql("""select tabItem.name,
		if(length(tabItem.item_name) > 50,
			concat(substr(tabItem.item_name, 1, 50), "..."), item_name) as item_name,
		tabItem.item_group, tabItem.brand
		{columns}
		from tabItem
		where tabItem.docstatus < 2
			{default_conditions}
			and (
				{scond}
				or tabItem.name IN (select parent from `tabItem Barcode` where barcode LIKE %(txt)s)
				{description_cond}
			)
			{fcond}
			{mcond}
			{has_applicable_items_cond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			if(locate(%(_txt)s, item_name), locate(%(_txt)s, item_name), 99999),
			idx desc,
			name, item_name
		limit %(start)s, %(page_len)s """.format(
			columns=columns,
			default_conditions=default_conditions,
			scond=searchfields,
			fcond=get_filters_cond(doctype, filters, conditions).replace('%', '%%'),
			mcond=get_match_cond(doctype).replace('%', '%%'),
			description_cond=description_cond,
			has_applicable_items_cond=has_applicable_items_cond),
			{
				"today": nowdate(),
				"txt": "%%%s%%" % txt,
				"_txt": txt.replace("%", ""),
				"start": start,
				"page_len": page_len
			}, as_dict=as_dict)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def service_template_query(doctype, txt, searchfield, start, page_len, filters):
	conditions = []
	fields = get_fields("Service Template", ["name", "service_template_name"])

	searchfields = frappe.get_meta("Service Template").get_search_fields()
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	applies_to_item = None
	applies_to_item_group = None
	if filters and isinstance(filters, dict):
		applies_to_item = filters.pop('applies_to_item', None)
		applies_to_item_group = filters.pop('applies_to_item_group', None)

	if applies_to_item:
		item_doc = frappe.get_cached_doc("Item", applies_to_item)

		applicable_item_codes = ["", item_doc.name]
		if item_doc.variant_of:
			applicable_item_codes.append(item_doc.variant_of)

		filters['applies_to_item'] = ['in', applicable_item_codes]

		applies_to_item_group = item_doc.item_group

	if applies_to_item_group or applies_to_item:
		applicable_item_groups = [""]
		if applies_to_item_group:
			applicable_item_groups.append(applies_to_item_group)
			applicable_item_groups += frappe.get_all("Item Group", filters={
				"name": ["ancestors of", applies_to_item_group]
			}, pluck="name")

		filters['applies_to_item_group'] = ['in', applicable_item_groups]

	frappe.utils.call_hook_method("extend_service_template_query_filters", filters, applies_to_item)

	return frappe.db.sql("""
			select {fields}
			from `tabService Template`
			where ({scond}) and disabled = 0
				{fcond}
				{mcond}
			order by
				if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
				idx desc, name
			limit %(start)s, %(page_len)s
		""".format(
		fields=", ".join(fields),
		scond=searchfields,
		fcond=get_filters_cond(doctype, filters, conditions).replace('%', '%%'),
		mcond=get_match_cond(doctype).replace('%', '%%'),
		key=searchfield,
	), {
		'txt': '%' + txt + '%',
		'_txt': txt.replace("%", ""),
		'start': start or 0,
		'page_len': page_len or 20
	})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def applicable_item_group(doctype, txt, searchfield, start, page_len, filters):
	conditions = []
	fields = get_fields("Item Group", ["name"])

	return frappe.db.sql("""
		select {fields}
		from `tabItem Group`
		where `tabItem Group`.`{key}` like %(txt)s
			and exists(select iai.name
				from `tabItem Applicable Item` iai
				inner join `tabItem` iaim on iaim.name = iai.applicable_item_code
				where iaim.item_group = `tabItem Group`.name)
			{fcond} {mcond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			idx desc, name
		limit %(start)s, %(page_len)s
	""".format(
			fields=", ".join(fields),
			fcond=get_filters_cond(doctype, filters, conditions).replace('%', '%%'),
			mcond=get_match_cond(doctype).replace('%', '%%'),
			key=searchfield
		), {
			'txt': '%' + txt + '%',
			'_txt': txt.replace("%", ""),
			'start': start or 0,
			'page_len': page_len or 20
		})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def bom(doctype, txt, searchfield, start, page_len, filters):
	conditions = []
	fields = get_fields("BOM", ["name", "item"])

	return frappe.db.sql("""select {fields}
		from tabBOM
		where tabBOM.docstatus=1
			and tabBOM.is_active=1
			and tabBOM.`{key}` like %(txt)s
			{fcond} {mcond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			creation desc,
			name desc
		limit %(start)s, %(page_len)s """.format(
			fields=", ".join(fields),
			fcond=get_filters_cond(doctype, filters, conditions).replace('%', '%%'),
			mcond=get_match_cond(doctype).replace('%', '%%'),
			key=searchfield),
		{
			'txt': '%' + txt + '%',
			'_txt': txt.replace("%", ""),
			'start': start or 0,
			'page_len': page_len or 20
		})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def workstation_query(doctype, txt, searchfield, start, page_len, filters):
	fields = get_fields("Workstation", ["name"])

	searchfields = frappe.get_meta("Workstation").get_search_fields()
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	exists_cond = ""
	operation = filters and filters.pop("operation", None)
	if operation:
		exists_cond = """ and (exists(
			select wop.name
			from `tabWorkstation Operation` wop
			where wop.parent = `tabWorkstation`.name and wop.operation = {0}
		) or not exists(
			select wop.name
			from `tabWorkstation Operation` wop
			where wop.parent = `tabWorkstation`.name
		))""".format(frappe.db.escape(operation))

	return frappe.db.sql("""
		select {fields}
		from `tabWorkstation`
		where ({scond}) {fcond} {mcond} {exists_cond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			idx desc,
			name
		limit %(start)s, %(page_len)s
	""".format(**{
		"fields": ", ".join(fields),
		"scond": searchfields,
		"exists_cond": exists_cond,
		"mcond": get_match_cond(doctype),
		"fcond": get_filters_cond(doctype, filters, []).replace('%', '%%'),
	}), {
		'txt': "%%%s%%" % txt,
		'_txt': txt.replace("%", ""),
		'start': start,
		'page_len': page_len
	})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_project_name(doctype, txt, searchfield, start, page_len, filters):
	cond = ''
	if filters.get('customer'):
		cond = """(`tabProject`.customer = %s or
			ifnull(`tabProject`.customer,"")="") and""" %(frappe.db.escape(filters.get("customer")))

	fields = get_fields("Project", ["name"])

	return frappe.db.sql("""select {fields} from `tabProject`
		where `tabProject`.status not in ("Completed", "Cancelled")
			and {cond} `tabProject`.name like %(txt)s {match_cond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			modified desc,
			`tabProject`.name asc
		limit {start}, {page_len}""".format(
			fields=", ".join(['`tabProject`.{0}'.format(f) for f in fields]),
			cond=cond,
			match_cond=get_match_cond(doctype),
			start=start,
			page_len=page_len), {
				"txt": "%{0}%".format(txt),
				"_txt": txt.replace('%', '')
			})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_delivery_notes_to_be_billed(doctype, txt, searchfield, start, page_len, filters, as_dict):
	return _get_delivery_notes_to_be_billed(doctype, txt, searchfield, start, page_len, filters, as_dict)


def _get_delivery_notes_to_be_billed(doctype="Delivery Note", txt="", searchfield="name", start=0, page_len=0,
		filters=None, as_dict=True, ignore_permissions=False):
	fields = get_fields("Delivery Note", ["name", "customer", "customer_name", "posting_date", "project"])
	select_fields = ", ".join(["`tabDelivery Note`.{0}".format(f) for f in fields])
	limit = "limit {0}, {1}".format(start, page_len) if page_len else ""

	if not filters:
		filters = {}

	claim_customer_cond = ""
	if isinstance(filters, dict) and cint(filters.get('claim_billing')):
		if filters.get('customer'):
			claim_customer_op = "dni.claim_customer = {0}".format(frappe.db.escape(filters.get('customer')))
			filters.pop("customer")
		else:
			claim_customer_op = "ifnull(dni.claim_customer, '') != ''"

		claim_customer_cond = """ and exists(select dni.name from `tabDelivery Note Item` dni
			where dni.parent = `tabDelivery Note`.name and {0})""".format(claim_customer_op)

	if "claim_billing" in filters:
		filters.pop("claim_billing")

	return frappe.db.sql("""
		select {fields}
		from `tabDelivery Note`
		left join `tabDelivery Note` dr on `tabDelivery Note`.is_return = 1 and dr.name = `tabDelivery Note`.return_against
		where `tabDelivery Note`.docstatus = 1
			and `tabDelivery Note`.`{key}` like {txt}
			and `tabDelivery Note`.`status` not in ('Stopped', 'Closed')
			and `tabDelivery Note`.billing_status = 'To Bill'
			and (`tabDelivery Note`.is_return = 0 or dr.billing_status = 'To Bill')
			{claim_customer_cond} {fcond} {mcond}
		order by `tabDelivery Note`.posting_date, `tabDelivery Note`.posting_time, `tabDelivery Note`.creation
		{limit}
	""".format(
		fields=select_fields,
		key=searchfield,
		fcond=get_filters_cond(doctype, filters, [], ignore_permissions=ignore_permissions),
		mcond="" if ignore_permissions else get_match_cond(doctype),
		claim_customer_cond=claim_customer_cond,
		limit=limit,
		txt="%(txt)s",
	), {"txt": ("%%%s%%" % txt)}, as_dict=as_dict)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_sales_orders_to_be_billed(doctype, txt, searchfield, start, page_len, filters, as_dict):
	return _get_sales_orders_to_be_billed(doctype, txt, searchfield, start, page_len, filters, as_dict)


def _get_sales_orders_to_be_billed(doctype="Sales Order", txt="", searchfield="name", start=0, page_len=0,
		filters=None, as_dict=True, ignore_permissions=False):
	fields = get_fields(doctype, ["name", "customer", "customer_name", "transaction_date", "project"])
	select_fields = ", ".join(["`tabSales Order`.{0}".format(f) for f in fields])
	limit = "limit {0}, {1}".format(start, page_len) if page_len else ""

	if not filters:
		filters = {}

	claim_customer_cond = ""
	if cint(filters.get('claim_billing')):
		if filters.get('customer'):
			claim_customer_op = "soi.claim_customer = {0}".format(frappe.db.escape(filters.get('customer')))
			filters.pop("customer")
		else:
			claim_customer_op = "ifnull(soi.claim_customer, '') != ''"

		claim_customer_cond = """ and exists(select soi.name from `tabSales Order Item` soi
			where soi.parent = `tabSales Order`.name and {0})""".format(claim_customer_op)

	if "claim_billing" in filters:
		filters.pop("claim_billing")

	return frappe.db.sql("""
		select {fields}
		from `tabSales Order`
		where `tabSales Order`.docstatus = 1
			and `tabSales Order`.`{key}` like {txt}
			and `tabSales Order`.`status` not in ('Closed', 'On Hold')
			and `tabSales Order`.billing_status = 'To Bill'
			{claim_customer_cond} {fcond} {mcond}
		order by `tabSales Order`.transaction_date, `tabSales Order`.creation
		{limit}
	""".format(
		fields=select_fields,
		key=searchfield,
		fcond=get_filters_cond(doctype, filters, [], ignore_permissions=ignore_permissions),
		mcond="" if ignore_permissions else get_match_cond(doctype),
		claim_customer_cond=claim_customer_cond,
		limit=limit,
		txt="%(txt)s",
	), {"txt": ("%%%s%%" % txt)}, as_dict=as_dict)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_packing_slips_to_be_delivered(doctype, txt, searchfield, start, page_len, filters, as_dict):
	return _get_packing_slips_to_be_delivered(doctype, txt, searchfield, start, page_len, filters, as_dict)


def _get_packing_slips_to_be_delivered(doctype="Packing Slip", txt="", searchfield="name", start=0, page_len=0,
		filters=None, as_dict=True):

	fields = get_fields("Packing Slip", [
		"name", "package_type", "warehouse", "posting_date",
		"customer", "customer_name",
		"total_net_weight", "total_qty", "total_stock_qty",
		"packed_items",
	])
	select_fields = ", ".join(["`tabPacking Slip`.{0}".format(f) for f in fields])
	limit = "limit {0}, {1}".format(start, page_len) if page_len else ""

	exists_conditions = []

	if filters.get("no_customer"):
		filters.pop("no_customer", None)
		filters["customer"] = ["is", "not set"]

	if filters.get("sales_order"):
		exists_conditions.append("`tabPacking Slip Item`.sales_order = {0}".format(
			frappe.db.escape(filters.pop("sales_order"))))

	if "sales_order_item" in filters:
		sales_order_items = filters.pop("sales_order_item")
		if sales_order_items:
			if not isinstance(sales_order_items, list):
				sales_order_items = [sales_order_items]

			exists_conditions.append("`tabPacking Slip Item`.sales_order_item in ({0})".format(
				", ".join([frappe.db.escape(i) for i in sales_order_items]),
			))

	if filters.get("item_code"):
		exists_conditions.append("`tabPacking Slip Item`.item_code = {0}".format(
			frappe.db.escape(filters.pop("item_code"))))

	if exists_conditions:
		exists_conditions = """ and exists(select `tabPacking Slip Item`.name from `tabPacking Slip Item` where
				`tabPacking Slip Item`.parent = `tabPacking Slip`.name and {0})""".format(
			" and ".join(exists_conditions))
	else:
		exists_conditions = ""

	return frappe.db.sql("""
			select {fields}
			from `tabPacking Slip`
			where `tabPacking Slip`.`{key}` like {txt}
				and `tabPacking Slip`.`status` = 'In Stock'
				{exists_conditions} {fcond} {mcond}
			order by `tabPacking Slip`.posting_date, `tabPacking Slip`.posting_time, `tabPacking Slip`.creation
			{limit}
		""".format(
		fields=select_fields,
		key=searchfield,
		exists_conditions=exists_conditions,
		fcond=get_filters_cond(doctype, filters, []),
		mcond=get_match_cond(doctype),
		limit=limit,
		txt="%(txt)s",
	), {"txt": ("%%%s%%" % txt)}, as_dict=as_dict)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_projects_to_be_billed(doctype="Project", txt="", searchfield="name", start=0, page_len=0,
		filters=None, as_dict=True, ignore_permissions=False):

	# Build Filters
	allowed_transaction_filters = ['claim_billing']
	exluded_custom_filters = ['name', 'project',
		'transaction_date', 'posting_date', 'project_date', 'customer']

	sales_order_meta = frappe.get_meta("Sales Order")
	delivery_note_meta = frappe.get_meta("Sales Order")
	project_meta = frappe.get_meta("Project")

	filters = frappe._dict(filters)
	sales_order_filters = frappe._dict()
	delivery_note_filters = frappe._dict()
	project_filters = frappe._dict()

	for f, v in filters.items():
		if (sales_order_meta.has_field(f) or f in allowed_transaction_filters) and f not in exluded_custom_filters:
			sales_order_filters[f] = v

		if (delivery_note_meta.has_field(f) or f in allowed_transaction_filters) and f not in exluded_custom_filters:
			delivery_note_filters[f] = v

		if project_meta.has_field(f) and f not in exluded_custom_filters:
			project_filters[f] = v

	delivery_note_filters['is_return'] = 0

	if filters.get('project'):
		project_filters['name'] = filters.get('project')
		sales_order_filters['project'] = filters.get('project')
		delivery_note_filters['project'] = filters.get('project')
	if filters.get('name'):
		project_filters['name'] = filters.get('name')
		sales_order_filters['project'] = filters.get('name')
		delivery_note_filters['project'] = filters.get('name')

	if filters.get('project_date'):
		project_filters['project_date'] = filters.get('project_date')

	if filters.get('customer'):
		sales_order_filters['customer'] = filters.get('customer')
		delivery_note_filters['customer'] = filters.get('customer')

	# Get Sales Orders and Delivery Notes
	sales_orders = _get_sales_orders_to_be_billed(filters=sales_order_filters)
	delivery_notes = _get_delivery_notes_to_be_billed(filters=delivery_note_filters)

	project_names = list(set([d.project for d in sales_orders if d.project] + [d.project for d in delivery_notes if d.project]))
	if not project_names:
		return []

	# Project Query
	fields = get_fields(doctype, ["name", "project_type", "customer", "customer_name", "project_date"])
	select_fields = ", ".join(["`tabProject`.{0}".format(f) for f in fields])
	limit = "limit {0}, {1}".format(start, page_len) if page_len else ""

	return frappe.db.sql("""
		select {fields}
		from `tabProject`
		where `tabProject`.`{key}` like {txt}
			and `tabProject`.`name` in %(project_names)s
			{fcond} {mcond}
		order by `tabProject`.project_date, `tabProject`.creation
		{limit}
	""".format(
		fields=select_fields,
		key=searchfield,
		fcond=get_filters_cond(doctype, project_filters, [], ignore_permissions=ignore_permissions),
		mcond="" if ignore_permissions else get_match_cond(doctype),
		limit=limit,
		txt="%(txt)s",
	), {"txt": ("%%%s%%" % txt), "project_names": project_names}, as_dict=as_dict)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_batch_no(doctype, txt, searchfield, start, page_len, filters):
	cond = ""
	if filters.get("posting_date"):
		cond += " and (batch.expiry_date is null or batch.expiry_date >= %(posting_date)s)"

	if filters.get("warehouse"):
		cond += " and sle.warehouse = %(warehouse)s"

	batch_nos = None
	args = {
		'item_code': filters.get("item_code"),
		'warehouse': filters.get("warehouse"),
		'posting_date': filters.get('posting_date'),
		'txt': "%{0}%".format(txt),
		"start": start,
		"page_len": page_len
	}

	if filters.get("show_all") or filters.get("is_return"):
		having_clause = ""
	elif filters.get("show_negative"):
		having_clause = "having sum(sle.actual_qty) != 0"
	else:
		having_clause = "having sum(sle.actual_qty) > 0"

	if filters.get("is_return") or filters.get('is_receipt'):
		having_clause = ""

	batch_nos = frappe.db.sql("""
		select sle.batch_no,
			sum(sle.actual_qty), sle.stock_uom,
			min(timestamp(sle.posting_date, sle.posting_time)) as received_dt,
			batch.manufacturing_date,
			batch.expiry_date
		from `tabStock Ledger Entry` sle
		inner join `tabBatch` batch on sle.batch_no = batch.name
		where
			batch.disabled = 0
			and sle.item_code = %(item_code)s
			and sle.batch_no like %(txt)s
			{cond}
		group by batch_no
		{having_clause}
		order by batch.expiry_date, received_dt, sle.batch_no desc
		limit %(start)s, %(page_len)s
	""".format(
		cond=cond,
		having_clause=having_clause
	), args, as_list=1)

	for d in batch_nos:
		d[1] = "{0} {1}".format(frappe.format(flt(d[1])), cstr(d[2]))  # Actual Qty
		d[2] = None  # UOM already formatted in Actual Qty
		d[3] = "Received: {0}".format(frappe.format(getdate(d[3]))) if d[3] else None  # Received Date
		d[4] = "Manufactured: {0}".format(frappe.format(getdate(d[4]))) if d[4] else None  # Manufactured Date
		d[5] = "Expiry: {0}".format(frappe.format(getdate(d[5]))) if d[5] else None  # Expiry Date

	return batch_nos


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_account_list(doctype, txt, searchfield, start, page_len, filters):
	filter_list = []

	if isinstance(filters, dict):
		for key, val in filters.items():
			if isinstance(val, (list, tuple)):
				filter_list.append([doctype, key, val[0], val[1]])
			else:
				filter_list.append([doctype, key, "=", val])
	elif isinstance(filters, list):
		filter_list.extend(filters)

	if "is_group" not in [d[1] for d in filter_list]:
		filter_list.append(["Account", "is_group", "=", "0"])

	if searchfield and txt:
		filter_list.append([doctype, searchfield, "like", "%%%s%%" % txt])

	return frappe.desk.reportview.execute("Account", filters = filter_list,
		fields = ["name", "parent_account"],
		limit_start=start, limit_page_length=page_len, as_list=True)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_blanket_orders(doctype, txt, searchfield, start, page_len, filters):
	return frappe.db.sql("""select distinct bo.name, bo.blanket_order_type, bo.to_date
		from `tabBlanket Order` bo, `tabBlanket Order Item` boi
		where
			boi.parent = bo.name
			and boi.item_code = {item_code}
			and bo.blanket_order_type = '{blanket_order_type}'
			and bo.company = {company}
			and bo.docstatus = 1"""
		.format(item_code = frappe.db.escape(filters.get("item")),
			blanket_order_type = filters.get("blanket_order_type"),
			company = frappe.db.escape(filters.get("company"))
		))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_income_account(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_match_cond

	# income account can be any Credit account,
	# but can also be a Asset account with account_type='Income Account' in special circumstances.
	# Hence the first condition is an "OR"
	if not filters: filters = {}

	condition = ""
	if filters.get("company"):
		condition += "and tabAccount.company = %(company)s"

	return frappe.db.sql("""select tabAccount.name from `tabAccount`
			where (tabAccount.report_type = "Profit and Loss"
					or tabAccount.account_type in ("Income Account", "Temporary"))
				and tabAccount.is_group=0
				and tabAccount.`{key}` LIKE %(txt)s
				{condition} {match_condition}
			order by idx desc, name"""
			.format(condition=condition, match_condition=get_match_cond(doctype), key=searchfield), {
				'txt': '%' + txt + '%',
				'company': filters.get("company", "")
			})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_expense_account(doctype, txt, searchfield, start, page_len, filters):
	from erpnext.controllers.queries import get_match_cond

	if not filters: filters = {}

	condition = ""
	if filters.get("company"):
		condition += "and tabAccount.company = %(company)s"

	return frappe.db.sql("""select tabAccount.name from `tabAccount`
		where (tabAccount.report_type = "Profit and Loss"
				or tabAccount.account_type in ("Expense Account", "Fixed Asset", "Temporary", "Asset Received But Not Billed", "Capital Work in Progress"))
			and tabAccount.is_group=0
			and tabAccount.docstatus!=2
			and tabAccount.{key} LIKE %(txt)s
			{condition} {match_condition}"""
		.format(condition=condition, key=searchfield,
			match_condition=get_match_cond(doctype)), {
			'company': filters.get("company", ""),
			'txt': '%' + txt + '%'
		})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	# Should be used when item code is passed in filters.
	conditions, bin_conditions = [], []
	filter_dict = get_doctype_wise_filters(filters)

	sub_query = """ select round(`tabBin`.actual_qty, 2) from `tabBin`
		where `tabBin`.warehouse = `tabWarehouse`.name
		{bin_conditions} """.format(
		bin_conditions=get_filters_cond(doctype, filter_dict.get("Bin"),
			bin_conditions, ignore_permissions=True))

	query = """select `tabWarehouse`.name,
		CONCAT_WS(" : ", "Actual Qty", ifnull( ({sub_query}), 0) ) as actual_qty
		from `tabWarehouse`
		where
		   `tabWarehouse`.`{key}` like {txt}
			{fcond} {mcond}
		order by
			`tabWarehouse`.name desc
		limit
			{start}, {page_len}
		""".format(
			sub_query=sub_query,
			key=searchfield,
			fcond=get_filters_cond(doctype, filter_dict.get("Warehouse"), conditions),
			mcond=get_match_cond(doctype),
			start=start,
			page_len=page_len,
			txt=frappe.db.escape('%{0}%'.format(txt))
		)

	return frappe.db.sql(query)


def get_doctype_wise_filters(filters):
	# Helper function to seperate filters doctype_wise
	filter_dict = defaultdict(list)
	for row in filters:
		filter_dict[row[0]].append(row)
	return filter_dict


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_batch_numbers(doctype, txt, searchfield, start, page_len, filters):
	query = """select batch_id from `tabBatch`
			where disabled = 0
			and (expiry_date >= CURDATE() or expiry_date IS NULL)
			and name like {txt}""".format(txt = frappe.db.escape('%{0}%'.format(txt)))

	if filters and filters.get('item'):
		query += " and item = {item}".format(item = frappe.db.escape(filters.get('item')))

	return frappe.db.sql(query, filters)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_uom_query(doctype, txt, searchfield, start, page_len, filters):
	if (
		filters
		and filters.get('item_code')
		and not frappe.get_cached_value("Item", filters.get('item_code'), "do_not_restrict_uom_selection")
	):
		from erpnext.stock.doctype.item.item import get_convertible_item_uoms
		convertible_uoms = get_convertible_item_uoms(filters.get('item_code'))
		if not convertible_uoms:
			return []

		res = frappe.db.sql("""
			select distinct `tabUOM`.name
			from `tabUOM`
			inner join `tabItem` on `tabItem`.name = %(item_code)s
			left join `tabUOM Conversion Detail` on (
				`tabUOM Conversion Detail`.parenttype = 'Item'
				and `tabUOM Conversion Detail`.parent = `tabItem`.name
				and `tabUOM Conversion Detail`.uom = `tabUOM`.name
			)
			where `tabUOM`.name like %(txt)s
				and `tabUOM`.name in %(convertible_uoms)s
				and `tabUOM`.disabled = 0
			order by
				if(locate(%(_txt)s, `tabUOM`.name), locate(%(_txt)s, `tabUOM`.name), 99999),
				if(`tabUOM`.name = `tabItem`.stock_uom, 0, 1),
				if(`tabUOM Conversion Detail`.idx is null, 99999, `tabUOM Conversion Detail`.idx),
				`tabUOM`.idx,
				`tabUOM`.name
			limit %(start)s, %(page_len)s
		""".format(**{
			'key': searchfield,
		}), {
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace("%", ""),
			'start': start,
			'page_len': page_len,
			'item_code': filters.get('item_code'),
			'convertible_uoms': convertible_uoms,
		})
		return res
	else:
		return frappe.db.sql("""
			select name
			from `tabUOM`
			where name like %(txt)s and disabled = 0
			order by
				if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
				idx desc,
				name
			limit %(start)s, %(page_len)s
		""", {
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace("%", ""),
			'start': start,
			'page_len': page_len,
		})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_manufacturer_query(doctype, txt, searchfield, start, page_len, filters):
	item_filters = [
		['manufacturer', 'like', '%' + txt + '%'],
		['item_code', '=', filters.get("item_code")]
	]

	item_manufacturers = frappe.get_all(
		"Item Manufacturer",
		fields=["manufacturer", "manufacturer_part_no"],
		filters=item_filters,
		limit_start=start,
		limit_page_length=page_len,
		as_list=1
	)
	return item_manufacturers


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_purchase_receipts(doctype, txt, searchfield, start, page_len, filters):
	query = """
		select pr.name
		from `tabPurchase Receipt` pr, `tabPurchase Receipt Item` pritem
		where pr.docstatus = 1 and pritem.parent = pr.name
		and pr.name like {txt}""".format(txt = frappe.db.escape('%{0}%'.format(txt)))

	if filters and filters.get('item_code'):
		query += " and pritem.item_code = {item_code}".format(item_code = frappe.db.escape(filters.get('item_code')))

	return frappe.db.sql(query, filters)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_purchase_invoices(doctype, txt, searchfield, start, page_len, filters):
	query = """
		select pi.name
		from `tabPurchase Invoice` pi, `tabPurchase Invoice Item` piitem
		where pi.docstatus = 1 and piitem.parent = pi.name
		and pi.name like {txt}""".format(txt = frappe.db.escape('%{0}%'.format(txt)))

	if filters and filters.get('item_code'):
		query += " and piitem.item_code = {item_code}".format(item_code = frappe.db.escape(filters.get('item_code')))

	return frappe.db.sql(query, filters)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def vehicle_brand_query(doctype, txt, searchfield, start, page_len, filters):
	fields = get_fields("Brand", ['name'])
	searchfields = frappe.get_meta("Brand").get_search_fields()
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	return frappe.db.sql("""
		select {fields}
		from `tabBrand`
		where exists(select tabItem.name from tabItem where tabItem.brand = tabBrand.name and tabItem.is_vehicle = 1)
			and ({scond}) {fcond} {mcond}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			idx desc
		limit %(start)s, %(page_len)s
		""".format(**{
		'fields': ", ".join(fields),
		'key': searchfield,
		'scond': searchfields,
		'fcond': get_filters_cond("Brand", filters, []).replace('%', '%%'),
		'mcond': get_match_cond(doctype),
	}), {
		'txt': "%%%s%%" % txt,
		'_txt': txt.replace("%", ""),
		'start': start,
		'page_len': page_len
	})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def vehicle_color_query(doctype, txt, searchfield, start, page_len, filters):
	conditions = []

	fields = get_fields("Vehicle Color", ['name', 'color_code'])
	searchfields = frappe.get_meta("Vehicle Color").get_search_fields()
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	item_condition = ""

	def add_vehicle_color_condition(item_doc):
		allowed = [frappe.db.escape(d.vehicle_color) for d in item_doc.vehicle_colors]
		return " and name in ({0})".format(", ".join(allowed))

	if isinstance(filters, dict) and 'item_code' in filters:
		item_code = filters.pop('item_code')
		if item_code:
			item = frappe.get_cached_doc("Item", item_code)
			if item:
				if item.vehicle_colors:
					item_condition = add_vehicle_color_condition(item)
				elif item.variant_of:
					variant_item = frappe.get_cached_doc("Item", item.variant_of)
					if variant_item and variant_item.vehicle_colors:
						item_condition = add_vehicle_color_condition(variant_item)

	return frappe.db.sql("""
		select {fields}
		from `tabVehicle Color`
		where ({scond}) {fcond} {mcond} {item_condition}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			idx desc
		limit %(start)s, %(page_len)s
	""".format(**{
		'fields': ", ".join(fields),
		'key': searchfield,
		'scond': searchfields,
		'fcond': get_filters_cond("Vehicle Color", filters, conditions).replace('%', '%%'),
		'mcond': get_match_cond(doctype),
		'item_condition': item_condition
	}), {
		'txt': "%%%s%%" % txt,
		'_txt': txt.replace("%", ""),
		'start': start,
		'page_len': page_len
	})


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def vehicle_interior_query(doctype, txt, searchfield, start, page_len, filters):
	conditions = []

	fields = get_fields("Vehicle Interior", ['name', 'interior_code'])
	searchfields = frappe.get_meta("Vehicle Interior").get_search_fields()
	searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

	item_condition = ""

	def add_vehicle_interior_condition(item_doc):
		allowed = [frappe.db.escape(d.vehicle_interior) for d in item_doc.vehicle_interiors]
		return " and name in ({0})".format(", ".join(allowed))

	if isinstance(filters, dict) and 'item_code' in filters:
		item_code = filters.pop('item_code')
		if item_code:
			item = frappe.get_cached_doc("Item", item_code)
			if item:
				if item.vehicle_interiors:
					item_condition = add_vehicle_interior_condition(item)
				elif item.variant_of:
					variant_item = frappe.get_cached_doc("Item", item.variant_of)
					if variant_item and variant_item.vehicle_interiors:
						item_condition = add_vehicle_interior_condition(variant_item)

	return frappe.db.sql("""
		select {fields}
		from `tabVehicle Interior`
		where ({scond}) {fcond} {mcond} {item_condition}
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			idx desc
		limit %(start)s, %(page_len)s
	""".format(**{
		'fields': ", ".join(fields),
		'key': searchfield,
		'scond': searchfields,
		'fcond': get_filters_cond("Vehicle Interior", filters, conditions).replace('%', '%%'),
		'mcond': get_match_cond(doctype),
		'item_condition': item_condition
	}), {
		'txt': "%%%s%%" % txt,
		'_txt': txt.replace("%", ""),
		'start': start,
		'page_len': page_len
	})


def get_fields(doctype, fields=None):
	if not fields:
		fields = []

	meta = frappe.get_meta(doctype)
	fields.extend(meta.get_search_fields())

	if meta.title_field and meta.title_field.strip() not in fields:
		fields.insert(1, meta.title_field.strip())

	return unique(fields)
