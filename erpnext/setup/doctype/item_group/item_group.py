# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet


class ItemGroup(NestedSet):
	nsm_parent_field = 'parent_item_group'

	def autoname(self):
		self.name = self.item_group_name

	def validate(self):
		if not self.parent_item_group and not frappe.flags.in_test:
			if frappe.db.exists("Item Group", _('All Item Groups')):
				self.parent_item_group = _('All Item Groups')

	def on_update(self):
		NestedSet.on_update(self)
		self.validate_one_root()

	def on_trash(self):
		NestedSet.on_trash(self)


def get_item_group_subtree(item_group, cache=True):
	def generator():
		return frappe.get_all("Item Group", filters={"name": ["subtree of", item_group]}, pluck="name")

	if cache:
		return frappe.local_cache("get_item_group_subtree", item_group, generator)
	else:
		return generator()


def get_item_group_print_heading(item_group):
	item_group_print_heading = item_group

	current_item_group = item_group
	while current_item_group:
		current_item_group_doc = frappe.get_cached_doc("Item Group", current_item_group)
		if current_item_group_doc.is_print_heading:
			item_group_print_heading = current_item_group_doc.get("print_heading") or current_item_group
			break

		current_item_group = current_item_group_doc.parent_item_group

	return item_group_print_heading
