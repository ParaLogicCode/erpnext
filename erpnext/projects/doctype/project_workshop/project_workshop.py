# -*- coding: utf-8 -*-
# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ProjectWorkshop(Document):
	def get_default_cost_center(self, company):
		if not company:
			return None

		for d in self.default_cost_centers:
			if d.company == company:
				return d.cost_center


@frappe.whitelist()
def get_project_workshop_details(project_workshop, company):
	doc = frappe.get_cached_doc("Project Workshop", project_workshop)

	out = frappe._dict()
	out.service_manager = doc.service_manager
	out.cost_center = doc.get_default_cost_center(company)

	out.document_checklist = []
	checklist = get_project_workshop_document_checklist_items(project_workshop)
	for item in checklist:
		out.document_checklist.append({'checklist_item': item.checklist_item, 'checklist_item_checked': 0, "is_mandatory": item.is_mandatory})

	return out


@frappe.whitelist()
def get_project_workshop_document_checklist_items(project_workshop):
	if not project_workshop:
		return []

	workshop_doc = frappe.get_cached_doc("Project Workshop", project_workshop)
	checklist_items = [d for d in workshop_doc.get('document_checklist')]
	return checklist_items
