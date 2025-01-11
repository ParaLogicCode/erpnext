# -*- coding: utf-8 -*-
# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

import frappe
import unittest

class TestServiceTemplate(unittest.TestCase):
	pass

def get_service_template():
	if not frappe.db.exists('Service Template', 'Test Service Template'):
		frappe.get_doc(dict(
			doctype = 'Service Template',
			name = 'Test Service Template',
			tasks = [
				dict(subject='Task 1', description='Task 1 description',
					start=0, duration=3),
				dict(subject='Task 2', description='Task 2 description',
					start=0, duration=2),
				dict(subject='Task 3', description='Task 3 description',
					start=2, duration=4),
				dict(subject='Task 4', description='Task 4 description',
					start=3, duration=2),
			]
		)).insert()

	return frappe.get_doc('Service Template', 'Test Service Template')

def make_service_template(service_template_name, project_tasks=[]):
	if not frappe.db.exists('Service Template', service_template_name):
		frappe.get_doc(dict(
			doctype = 'Service Template',
			name = service_template_name,
			tasks = project_tasks or [
				dict(subject='Task 1', description='Task 1 description',
					start=0, duration=3),
				dict(subject='Task 2', description='Task 2 description',
					start=0, duration=2),
				dict(subject='Task 3', description='Task 3 description',
					start=2, duration=4),
				dict(subject='Task 4', description='Task 4 description',
					start=3, duration=2),
			]
		)).insert()

	return frappe.get_doc('Service Template', service_template_name)