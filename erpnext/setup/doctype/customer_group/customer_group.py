# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils.nestedset import NestedSet, get_root_of


class CustomerGroup(NestedSet):
	nsm_parent_field = 'parent_customer_group'

	def validate(self):
		if not self.parent_customer_group:
			self.parent_customer_group = get_root_of("Customer Group")

	def on_update(self):
		super(CustomerGroup, self).on_update()
		self.validate_one_root()


def get_parent_customer_groups(customer_group):
	lft, rgt = frappe.db.get_value("Customer Group", customer_group, ['lft', 'rgt'])

	return frappe.db.sql("""
		select name
		from `tabCustomer Group`
		where lft <= %s and rgt >= %s
		order by lft asc
	""", (lft, rgt), as_dict=True)


def get_customer_group_subtree(customer_group, cache=True):
	def generator():
		return frappe.get_all("Customer Group", filters={"name": ["subtree of", customer_group]}, pluck="name")

	if cache:
		return frappe.local_cache("get_customer_group_subtree", customer_group, generator)
	else:
		return generator()


def on_doctype_update():
	frappe.db.add_index("Customer Group", ["lft", "rgt"])