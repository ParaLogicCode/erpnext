# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import cint, getdate, get_datetime, get_time, flt
from erpnext.controllers.status_updater import StatusUpdaterERP


class POSOpeningEntry(StatusUpdaterERP):
	def validate(self):
		self.set_date_and_time()
		self.set_pos_profile_details()
		self.validate_cashier()
		self.validate_duplicate()
		self.calculate_cash_denominations()
		self.validate_cash_amount()
		self.set_status()

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def set_date_and_time(self):
		now_dt = get_datetime()
		if not self.amended_from or not self.period_start_date or not self.period_start_time:
			self.period_start_date = getdate(now_dt)
			self.period_start_time = get_time(now_dt)

	def set_pos_profile_details(self):
		details = get_pos_profile_details(self.pos_profile)
		self.company = details.company
		self.branch = details.branch

	def validate_cashier(self):
		if not cint(frappe.db.get_value("User", self.user, "enabled")):
			frappe.throw(_("User {} is disabled. Please select valid user/cashier").format(self.user))

	def validate_duplicate(self):
		filters = {
			"user": self.user,
			"pos_profile": self.pos_profile,
			"status": "Open",
			"docstatus": 1,
		}
		if not self.is_new():
			filters["name"] = ["!=", self.name]

		existing = frappe.db.get_value("POS Opening Entry", filters=filters)
		if existing:
			frappe.throw(_("A POS Opening Entry is already Open for Cashier {0} and POS Profile {1}, please create POS Closing Entry first").format(
				frappe.bold(self.user), frappe.bold(self.pos_profile)
			))

	def calculate_cash_denominations(self):
		self.total_cash = 0
		for d in self.cash_denominations:
			d.amount = flt(d.denomination) * cint(d.count)
			self.total_cash += d.amount

	def validate_cash_amount(self):
		for d in self.balance_details:
			d.type = frappe.get_cached_value("Mode of Payment", d.mode_of_payment, "type")
			if d.type == "Cash" and self.total_cash:
				if flt(d.opening_amount) != flt(self.total_cash):
					frappe.throw(_("Row #{0}: Mode of Payment {1} should match Total Cash {2}").format(
						d.idx, d.mode_of_payment, self.get_formatted("total_cash")
					))

	def set_status(self, update=False, status=None, update_modified=True):
		previous_status = self.status

		if self.docstatus == 0:
			self.status = "Draft"
		elif self.docstatus == 1:
			pos_closing = frappe.db.get_value(
				"POS Closing Entry",
				filters={"pos_opening_entry": self.name, "docstatus": 1},
				fieldname=["name", "period_end_date", "period_end_time"],
				as_dict=True
			)
			if pos_closing:
				self.status = "Closed"
				self.period_end_date = pos_closing.period_end_date
				self.period_end_time = pos_closing.period_end_time
			else:
				self.status = "Open"
				self.period_end_date = None
				self.period_end_time = None

		elif self.docstatus == 2:
			self.status = "Cancelled"

		self.add_status_comment(previous_status)

		if update:
			self.db_set({
				"status": self.status,
				"period_end_date": self.period_end_date,
				"period_end_time": self.period_end_time,
			}, update_modified=update_modified)


@frappe.whitelist()
def get_pos_profile_details(pos_profile):
	out = frappe._dict()
	if not pos_profile:
		return out

	pos = frappe.get_cached_doc("POS Profile", pos_profile)

	out.company = pos.company
	out.branch = pos.branch

	out.balance_details = []
	for d in pos.payments:
		if not d.exclude_in_pos_opening:
			out.balance_details.append({
				"mode_of_payment": d.mode_of_payment,
				"type": frappe.get_cached_value("Mode of Payment", d.mode_of_payment, "type"),
			})

	return out


def get_pos_opening_entry(user, pos_profile):
	if not user or not pos_profile:
		return None

	pos_opening_entry = frappe.db.get_value("POS Opening Entry", filters={
		"user": user,
		"pos_profile": pos_profile,
		"status": "Open",
		"docstatus": 1,
	})
	return pos_opening_entry
