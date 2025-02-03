# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class TimesheetDetail(Document):
	pass


def on_doctype_update():
	frappe.db.add_index("Timesheet Detail", ["from_time", "to_time"])
