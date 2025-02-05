# -*- coding: utf-8 -*-
# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, clean_whitespace, cstr
import json


class ServiceTemplate(Document):
	def validate(self):
		self.validate_items()
		self.validate_duplicate_applicable_item_groups()
		self.validate_due_after()
		self.validate_service_warranty()

	def onload(self):
		pass

	def validate_items(self):
		for d in self.sales_items:
			if d.applicable_item_code:
				is_sales_item = frappe.get_cached_value("Item", d.applicable_item_code, "is_sales_item")
				if not is_sales_item:
					frappe.throw(_("Row #{0}: Item {1} is not a Sales Item").format(d.idx, d.applicable_item_code))

			d.selection_group = clean_whitespace(d.selection_group)

		for d in self.consumable_items:
			if d.applicable_item_code:
				is_stock_item = frappe.get_cached_value("Item", d.applicable_item_code, "is_stock_item")
				if not is_stock_item:
					frappe.throw(_("Row #{0}: Item {1} is not a Stock Item").format(d.idx, d.applicable_item_code))

	def validate_duplicate_applicable_item_groups(self):
		visited = set()
		for d in self.applicable_item_groups:
			if d.applicable_item_group in visited:
				frappe.throw(_("Row #{0}: Duplicate Applicable Item Group {1}")
					.format(d.idx, frappe.bold(d.applicable_item_group)))

			visited.add(d.applicable_item_group)

	def validate_due_after(self):
		if self.next_service_template:
			if not cint(self.next_due_after):
				frappe.throw(_("Please set Next Maintenance Due After"))
		else:
			self.next_due_after = 0

		if cint(self.due_after_delivery_date) < 0:
			frappe.throw(_("Due After Delivery Date cannot be negative"))

		if cint(self.next_due_after) < 0:
			frappe.throw(_("Next Maintenance Due After cannot be negative"))

	def validate_service_warranty(self):
		if not self.includes_service_warranty:
			return

		if cint(self.warranty_validity) <= 0:
			frappe.throw(_("Please set Warranty Validity"))

	def filter_applicable_item(self, pt_item, applies_to_item=None, applies_to_customer=None):
		from erpnext.setup.doctype.item_group.item_group import get_item_group_subtree
		from erpnext.setup.doctype.customer_group.customer_group import get_customer_group_subtree

		applies_to_item_doc = frappe.get_cached_doc("Item", applies_to_item) if applies_to_item else frappe._dict()
		applies_to_customer_doc = frappe.get_cached_doc("Customer", applies_to_customer) if applies_to_customer else frappe._dict()

		if pt_item.get("applies_to_item"):
			if pt_item.get("applies_to_item") != applies_to_item:
				return True

		if pt_item.get("applies_to_item_group"):
			applicable_item_groups = get_item_group_subtree(pt_item.get("applies_to_item_group"))
			if applies_to_item_doc.item_group not in applicable_item_groups:
				return True

		if pt_item.get("applies_to_customer_group"):
			applicable_customer_groups = get_customer_group_subtree(pt_item.get("applies_to_customer_group"))
			if applies_to_customer_doc.customer_group not in applicable_customer_groups:
				return True

		return False


@frappe.whitelist()
def get_service_template_details(service_template):
	out = frappe._dict()
	if not service_template:
		return out

	template_doc = frappe.get_cached_doc("Service Template", service_template)
	out.service_template_name = template_doc.service_template_name

	frappe.utils.call_hook_method("get_service_template_details", service_template, out)

	return out


@frappe.whitelist()
def add_service_template_items(
	target_doc,
	service_template,
	applies_to_item=None,
	applies_to_customer=None,
	item_group=None,
	items_type=None,
	check_duplicate=False,
	service_template_detail=None,
	postprocess=True,
):
	from erpnext.stock.doctype.item_applicable_item.item_applicable_item import add_applicable_items,\
		append_applicable_items

	if isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))

	service_template_doc = frappe.get_cached_doc("Service Template", service_template)

	if not service_template_detail and service_template:
		service_template_detail = frappe._dict({
			'service_template': service_template,
			'service_template_name': service_template_doc.service_template_name,
		})

	if not target_doc.meta.has_field('items'):
		frappe.throw(_("Target document does not have items table"))

	# remove first empty row
	if target_doc.get('items') and not target_doc.items[0].item_code and not target_doc.items[0].item_name:
		target_doc.remove(target_doc.items[0])

	consumable_items = cint(target_doc.doctype in ("Material Request", "Stock Entry"))
	items_table = "consumable_items" if consumable_items else "sales_items"

	# get applicable items from service template
	service_template_items = get_service_template_items(
		service_template, items_table,
		applies_to_item=applies_to_item, applies_to_customer=applies_to_customer,
		item_group=item_group, items_type=items_type
	)

	append_applicable_items(target_doc, service_template_items, check_duplicate=check_duplicate,
		service_template_detail=service_template_detail)

	# get applicable items from item master
	if applies_to_item and not consumable_items:
		applicable_items_groups = [
			d.applicable_item_group
			for d in service_template_doc.applicable_item_groups
			if (not item_group or d.applicable_item_group == item_group)
		]

		if applicable_items_groups:
			target_doc = add_applicable_items(target_doc, applies_to_item, item_groups=applicable_items_groups,
				items_type=items_type, check_duplicate=check_duplicate, service_template_detail=service_template_detail,
				postprocess=False)

	# postprocess
	if postprocess:
		target_doc.run_method("postprocess_after_mapping")

	return target_doc


def get_service_template_items(
	service_template,
	items_table,
	applies_to_item=None,
	applies_to_customer=None,
	item_group=None,
	items_type=None,
):
	from erpnext.stock.doctype.item_applicable_item.item_applicable_item import filter_applicable_item

	service_template_doc = frappe.get_cached_doc("Service Template", service_template)

	item_groups = []
	if item_group:
		item_groups.append(item_group)

	service_template_items = []

	selection_groups_selected = set()

	for pt_item in service_template_doc.get(items_table):
		selection_group = cstr(pt_item.get("selection_group")).upper()
		if selection_group and selection_group in selection_groups_selected:
			continue

		if filter_applicable_item(pt_item, item_groups, items_type=items_type):
			continue
		if service_template_doc.filter_applicable_item(pt_item, applies_to_item, applies_to_customer):
			continue

		service_template_items.append(pt_item)

		if selection_group:
			selection_groups_selected.add(selection_group)

	return service_template_items


def get_service_template_tasks(
	service_template,
	service_template_detail=None,
):
	tasks = []

	service_template_doc = frappe.get_cached_doc("Service Template", service_template)

	if not service_template_detail and service_template:
		service_template_detail = frappe._dict({
			'service_template': service_template,
			'service_template_name': service_template_doc.service_template_name,
		})

	for template_task_row in service_template_doc.tasks:
		task_details = frappe._dict()
		task_details.subject = template_task_row.subject
		task_details.description = template_task_row.description
		task_details.task_type = template_task_row.task_type
		task_details.expected_time = template_task_row.expected_time
		task_details.service_template = service_template_detail.service_template
		task_details.service_template_detail = service_template_detail.name
		task_details.determine_time = template_task_row.determine_time

		if template_task_row.use_template_name:
			task_details.subject = service_template_detail.service_template_name
		if template_task_row.use_template_description:
			task_details.description = service_template_detail.description

		frappe.utils.call_hook_method(
			"update_service_template_task_details",
			task_details=task_details,
			service_template=service_template,
			service_template_detail=service_template_detail,
		)

		tasks.append(task_details)

	return tasks


@frappe.whitelist()
def guess_service_template(service_template_category, applies_to_item):
	service_template = frappe.db.get_value("Service Template", {
		'service_template_category': service_template_category,
		'applies_to_item': applies_to_item
	})

	if not service_template:
		applies_to_variant_of = frappe.get_cached_value("Item", applies_to_item, "variant_of")
		if applies_to_variant_of:
			service_template = frappe.db.get_value("Service Template", {
				'service_template_category': service_template_category,
				'applies_to_item': applies_to_variant_of
			})

	return service_template