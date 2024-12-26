# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate, get_datetime, get_time
from frappe.model.document import Document
from erpnext.accounts.doctype.pos_opening_entry.pos_opening_entry import get_pos_opening_entry


class POSClosingEntry(Document):
	def validate(self):
		self.set_closing_voucher_details()

	def on_submit(self):
		self.update_pos_opening_entry()

	def on_cancel(self):
		self.update_pos_opening_entry()

	@frappe.whitelist()
	def set_closing_voucher_details(self):
		if not self.user or not self.pos_profile or not self.company:
			return

		self.set_pos_profile_details()

		self.pos_opening_entry = get_pos_opening_entry(self.user, self.pos_profile)
		pos_opening = frappe.get_doc("POS Opening Entry", self.pos_opening_entry) if self.pos_opening_entry else frappe._dict()

		self.set_date_and_time(pos_opening)

		invoices = self.get_invoices()
		self.set_invoice_list(invoices)
		self.set_sales_summary_values(invoices)

		if self.docstatus == 0:
			self.set_mode_of_payments(invoices, pos_opening)

		self.set_difference()

		taxes = get_tax_details(invoices)
		self.set_taxes(taxes)

	def set_pos_profile_details(self):
		if self.pos_profile:
			pos_profile = frappe.get_cached_doc("POS Profile", self.pos_profile)
			self.company = pos_profile.company
			self.branch = pos_profile.branch

	def set_date_and_time(self, pos_opening):
		now_dt = get_datetime()
		now_date = getdate(now_dt)

		if not self.period_start_date:
			self.period_start_date = now_date
		if not self.period_end_date:
			self.period_end_date = now_date

		if pos_opening:
			self.period_start_date = pos_opening.period_start_date
			self.period_start_time = pos_opening.period_start_time

		if not pos_opening and getdate(self.period_start_date) == now_date:
			self.period_start_time = get_time(now_dt)
		if getdate(self.period_end_date) == now_date:
			self.period_end_time = get_time(now_dt)

	def get_invoices(self):
		return frappe.db.sql("""
			select inv.name,
				inv.base_grand_total as grand_total,
				inv.base_rounded_total as rounded_total,
				inv.base_net_total as net_total,
				inv.pos_total_qty
			from `tabSales Invoice` inv
			where inv.docstatus = 1
			and inv.is_pos = 1
			and inv.posting_date between %(from_date)s and %(to_date)s
			and inv.company = %(company)s
			and inv.pos_profile = %(pos_profile)s
			and inv.owner = %(user)s
		""", {
			"pos_profile": self.pos_profile,
			"user": self.user or frappe.session.user,
			"from_date": getdate(self.period_start_date),
			"to_date": getdate(self.period_start_date),
			"company": self.company,
		}, as_dict=1)

	def set_invoice_list(self, invoices):
		self.sales_invoices_summary = []
		for invoice in invoices:
			self.append('sales_invoices_summary', {
				'invoice': invoice['name'],
				'qty_of_items': invoice['pos_total_qty'],
				'net_total': invoice['net_total'],
				'grand_total': invoice['grand_total'],
				'rounded_total': invoice['rounded_total'],
			})

	def set_sales_summary_values(self, invoices):
		self.net_total = sum(item['net_total'] for item in invoices)
		self.grand_total = sum(item['rounded_total'] or item['grand_total'] for item in invoices)
		self.total_quantity = sum(item['pos_total_qty'] for item in invoices)

	def set_mode_of_payments(self, invoices, pos_opening):
		def get_row(mode_of_payment):
			row = [d for d in self.payment_reconciliation if d.mode_of_payment == mode_of_payment]
			if row:
				row = row[0]
			else:
				row = self.append('payment_reconciliation', {'mode_of_payment': mode_of_payment})

			return row

		# Calculate Previous Values
		user_closing_amounts = {}
		for row in self.get('payment_reconciliation'):
			if row.mode_of_payment:
				user_closing_amounts.setdefault(row.mode_of_payment, 0)
				user_closing_amounts[row.mode_of_payment] += flt(row.closing_amount)

		# Reset Mode of Payments Table
		self.payment_reconciliation = []

		# Set Opening Amounts
		for op in pos_opening.get("balance_details") or []:
			if not op.opening_amount:
				continue

			row = get_row(op.mode_of_payment)
			row.opening_amount = op.opening_amount

		# Collected Amount
		system_collected_amount = get_mode_of_payment_details(invoices)
		for mop in system_collected_amount:
			row = get_row(mop.name)
			row.collected_amount = mop.amount

		# Previous Closing Values
		for mode_of_payment, closing_amount in user_closing_amounts.items():
			row = get_row(mode_of_payment)
			row.closing_amount = closing_amount

		# Expected Amount
		for row in self.get('payment_reconciliation'):
			row.expected_amount = flt(row.opening_amount) + flt(row.collected_amount)

	def set_taxes(self, taxes):
		self.taxes = []
		for tax in taxes:
			self.append('taxes', {
				'account_head': tax['account_head'],
				'rate': tax['rate'],
				'amount': tax['amount']
			})

	def validate_duplicate(self):
		user = frappe.get_all('POS Closing Entry',
			filters={
				'user': self.user,
				'docstatus': 1
			},
			or_filters={
				'period_start_date': ('between', [self.period_start_date, self.period_end_date]),
				'period_end_date': ('between', [self.period_start_date, self.period_end_date])
			}
		)

		if user:
			frappe.throw(_("POS Closing Entry alreday exists for {0} between date {1} and {2}"
				.format(self.user, self.period_start_date, self.period_end_date)))

	def set_difference(self):
		for d in self.payment_reconciliation:
			d.difference = flt(d.closing_amount) - flt(d.expected_amount)

	def update_pos_opening_entry(self):
		if self.pos_opening_entry:
			doc = frappe.get_doc("POS Opening Entry", self.pos_opening_entry)
			doc.set_status(update=True)
			doc.notify_update()


def get_mode_of_payment_details(invoices):
	mode_of_payment_details = []
	if not invoices:
		return mode_of_payment_details

	invoice_names = [d.name for d in invoices]

	invoice_payment_data = frappe.db.sql("""
		select ifnull(pay.mode_of_payment, '') as mode_of_payment, sum(pay.base_amount) as paid_amount
		from `tabSales Invoice Payment` pay
		inner join `tabSales Invoice` inv on inv.name = pay.parent
		where inv.name in %s
		group by mode_of_payment
	""", [invoice_names], as_dict=1)

	payment_entry_data = frappe.db.sql("""
		select ifnull(pe.mode_of_payment, '') as mode_of_payment, sum(pe.base_paid_amount) as paid_amount
		from `tabPayment Entry Reference` pref
		inner join `tabPayment Entry` pe on pe.name = pref.parent
		where pe.docstatus = 1 and pref.reference_doctype = 'Sales Invoice' and pref.reference_name in %s
		group by mode_of_payment
	""", [invoice_names], as_dict=1)

	journal_entry_data = frappe.db.sql("""
		select ifnull(je.mode_of_payment, '') as mode_of_payment, sum(jea.credit - jea.debit) as paid_amount
		from `tabJournal Entry Account` jea
		inner join `tabJournal Entry` je on je.name = jea.parent
		where je.docstatus = 1 and jea.reference_type = 'Sales Invoice' and jea.reference_name in %s
		group by mode_of_payment
	""", [invoice_names], as_dict=1)

	invoice_change_data = frappe.db.sql("""
		select ifnull(pay.mode_of_payment, '') as mode_of_payment, sum(inv.base_change_amount) as change_amount
		from `tabSales Invoice` inv
		inner join `tabSales Invoice Payment` pay on pay.parent = inv.name
		where inv.name in %s and pay.type = 'Cash' and inv.base_change_amount != 0
		group by mode_of_payment
	""", [invoice_names], as_dict=1)

	payment_details = {}
	for d in invoice_payment_data + payment_entry_data + journal_entry_data:
		payment_details.setdefault(d["mode_of_payment"], 0)
		payment_details[d.mode_of_payment] += d.paid_amount

	for d in invoice_change_data:
		payment_details.setdefault(d["mode_of_payment"], 0)
		payment_details[d.mode_of_payment] -= d.change_amount

	for mode_of_payment, amount in payment_details.items():
		mode_of_payment_details.append(frappe._dict({'name': mode_of_payment, 'amount': amount}))

	return mode_of_payment_details


def get_tax_details(invoices):
	from erpnext.selling.report.sales_details.sales_details import get_itemised_taxes

	if not invoices:
		return []

	invoice_names = [d.name for d in invoices]

	sales_invoice_items = frappe.db.sql("""
		select i.name, i.parent, i.item_tax_detail, i.item_tax_rate
		from `tabSales Invoice Item` i
		where i.parent in %s
	""", [invoice_names], as_dict=1)

	itemised_tax, tax_columns = get_itemised_taxes(
		sales_invoice_items,
		"Sales Taxes and Charges",
		get_description_as_tax_head=False
	)

	tax_breakup = {}
	for d in sales_invoice_items:
		for account_head in tax_columns:
			tax_amount = itemised_tax.get(d.name, {}).get(account_head, {}).get("tax_amount", 0.0)
			tax_rate = itemised_tax.get(d.name, {}).get(account_head, {}).get("tax_rate", 0.0)

			key = (account_head, tax_rate)
			tax_breakup.setdefault(key, 0)
			tax_breakup[key] += tax_amount

	out = []
	for (account_head, tax_rate), tax_amount in tax_breakup.items():
		out.append(frappe._dict({'account_head': account_head, "rate": tax_rate, 'amount': tax_amount}))

	out = sorted(out, key=lambda d: d['rate'], reverse=1)
	return out
