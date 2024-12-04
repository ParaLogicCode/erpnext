# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document

class InsuranceSurveyor(Document):
	def validate(self):
		self.validate_contact_no()

	def validate_contact_no(self):
		from frappe.regional.regional import validate_mobile_no
		validate_mobile_no(self.insurance_surveyor_mobile_no)