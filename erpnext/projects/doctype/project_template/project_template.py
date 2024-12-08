# -*- coding: utf-8 -*-
# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint
import json


class ProjectTemplate(Document):
	def validate(self):
		self.validate_duplicate_items()
		self.validate_duplicate_applicable_item_groups()
		self.validate_due_after()

	def validate_duplicate_items(self):
		visited = set()
		for d in self.applicable_items:
			if d.applicable_item_code in visited:
				frappe.throw(_("Row #{0}: Duplicate Applicable Item {1}")
					.format(d.idx, frappe.bold(d.applicable_item_code)))

			visited.add(d.applicable_item_code)

	def validate_duplicate_applicable_item_groups(self):
		visited = set()
		for d in self.applicable_item_groups:
			if d.applicable_item_group in visited:
				frappe.throw(_("Row #{0}: Duplicate Applicable Item Group {1}")
					.format(d.idx, frappe.bold(d.applicable_item_group)))

			visited.add(d.applicable_item_group)

	def validate_due_after(self):
		if self.next_project_template:
			if not cint(self.next_due_after):
				frappe.throw(_("Please set Next Maintenance Due After"))
		else:
			self.next_due_after = 0

		if cint(self.due_after_delivery_date) < 0:
			frappe.throw(_("Due After Delivery Date cannot be negative"))

		if cint(self.next_due_after) < 0:
			frappe.throw(_("Next Maintenance Due After cannot be negative"))


@frappe.whitelist()
def get_project_template_details(project_template):
	out = frappe._dict()
	if not project_template:
		return out

	template_doc = frappe.get_cached_doc("Project Template", project_template)
	out.project_template_name = template_doc.project_template_name

	frappe.utils.call_hook_method("get_project_template_details", project_template, out)

	return out


@frappe.whitelist()
def add_project_template_items(
	target_doc,
	project_template,
	applies_to_item=None,
	item_group=None,
	items_type=None,
	check_duplicate=True,
	project_template_detail=None,
	postprocess=True
):
	from erpnext.stock.doctype.item_applicable_item.item_applicable_item import add_applicable_items,\
		append_applicable_items

	if isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))

	if not project_template_detail and project_template:
		project_template_detail = frappe._dict({'project_template': project_template})

	if not target_doc.meta.has_field('items'):
		frappe.throw(_("Target document does not have items table"))

	# remove first empty row
	if target_doc.get('items') and not target_doc.items[0].item_code and not target_doc.items[0].item_name:
		target_doc.remove(target_doc.items[0])

	use_stock_entry = cint(target_doc.doctype in ("Material Request", "Stock Entry"))

	project_template_doc = frappe.get_cached_doc("Project Template", project_template)

	# get applicable items from item master
	if applies_to_item:
		applicable_items_groups = [
			d.applicable_item_group
			for d in project_template_doc.applicable_item_groups
			if (not item_group or d.applicable_item_group == item_group) and cint(d.use_stock_entry) == use_stock_entry
		]

		if applicable_items_groups:
			target_doc = add_applicable_items(target_doc, applies_to_item, item_groups=applicable_items_groups,
				items_type=items_type, check_duplicate=check_duplicate, project_template_detail=project_template_detail,
				postprocess=False)

	# get applicable items from project template
	project_template_items = get_project_template_items(project_template, item_group=item_group, items_type=items_type)
	project_template_items = [d for d in project_template_items if cint(d.use_stock_entry) == use_stock_entry]

	append_applicable_items(target_doc, project_template_items, check_duplicate=check_duplicate,
		project_template_detail=project_template_detail)

	# postprocess
	if postprocess:
		target_doc.run_method("postprocess_after_mapping")

	return target_doc


def get_project_template_items(project_template, item_group=None, items_type=None):
	from erpnext.stock.doctype.item_applicable_item.item_applicable_item import filter_applicable_item

	project_template_doc = frappe.get_cached_doc("Project Template", project_template)

	item_groups = []
	if item_group:
		item_groups.append(item_group)

	project_template_items = []

	for pt_item in project_template_doc.applicable_items:
		if filter_applicable_item(pt_item, item_groups, items_type=items_type):
			continue

		project_template_items.append(pt_item)

	return project_template_items


@frappe.whitelist()
def guess_project_template(project_template_category, applies_to_item):
	project_template = frappe.db.get_value("Project Template", {
		'project_template_category': project_template_category,
		'applies_to_item': applies_to_item
	})

	if not project_template:
		applies_to_variant_of = frappe.get_cached_value("Item", applies_to_item, "variant_of")
		if applies_to_variant_of:
			project_template = frappe.db.get_value("Project Template", {
				'project_template_category': project_template_category,
				'applies_to_item': applies_to_variant_of
			})

	return project_template
