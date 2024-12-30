# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
import erpnext
from frappe import _
from frappe.utils import flt, getdate, get_datetime, get_time, cint, cstr
from frappe.model.document import Document
from erpnext.accounts.doctype.pos_opening_entry.pos_opening_entry import get_pos_opening_entry


class POSClosingEntry(Document):
	def validate(self):
		self.validate_pos_is_open(throw=True)
		self.set_closing_voucher_details()
		self.calculate_cash_denominations()
		self.validate_closing_amounts()

	def on_submit(self):
		self.update_pos_opening_entry()

	def on_cancel(self):
		self.update_pos_opening_entry()

	def before_print(self, print_settings=None):
		self.company_address_doc = erpnext.get_company_address_doc(self)

	def validate_pos_is_open(self, throw=True):
		from erpnext.accounts.doctype.pos_profile.pos_profile import check_is_pos_open
		if self.pos_profile and self.user:
			check_is_pos_open(self.user, self.pos_profile, throw=throw)

	def validate_closing_amounts(self):
		for d in self.payment_reconciliation:
			d.type = frappe.get_cached_value("Mode of Payment", d.mode_of_payment, "type")

			if d.expected_amount and not d.closing_amount:
				frappe.throw(_("Row #{0}: Please set Closing Amount for Mode of Payment {1}").format(
					d.idx, frappe.bold(d.mode_of_payment)
				))

			if d.type == "Cash" and self.total_cash:
				if flt(d.closing_amount) != flt(self.total_cash):
					frappe.throw(_("Row #{0}: Mode of Payment {1} should match Total Cash {2}").format(
						d.idx, d.mode_of_payment, self.get_formatted("total_cash")
					))

	@frappe.whitelist()
	def set_closing_voucher_details(self):
		if not self.user or not self.pos_profile or not self.company:
			return

		self.set_cash_denominations()
		self.set_pos_profile_details()
		self.validate_pos_is_open(throw=False)

		self.pos_opening_entry = get_pos_opening_entry(self.user, self.pos_profile)
		pos_opening = frappe.get_doc("POS Opening Entry", self.pos_opening_entry) if self.pos_opening_entry else frappe._dict()

		self.set_date_and_time(pos_opening)

		invoices = self.get_invoices()
		payment_details, payment_summary = get_pos_payment_details(invoices)

		self.set_payment_details(payment_details)
		self.set_payment_reconciliation(payment_summary, pos_opening)
		self.set_summary_values(invoices)

		self.set_accounts()
		self.calculate_totals()

		taxes = get_tax_details(invoices)
		self.set_taxes(taxes)

	def set_cash_denominations(self):
		from erpnext.accounts.doctype.pos_profile.pos_profile import get_cash_denominations
		if not self.cash_denominations:
			denominations = get_cash_denominations()
			for d in denominations:
				self.append("cash_denominations", d)

	def set_pos_profile_details(self):
		if self.pos_profile:
			pos_profile = frappe.get_cached_doc("POS Profile", self.pos_profile)
			self.company = pos_profile.company
			self.branch = pos_profile.branch

	def set_date_and_time(self, pos_opening):
		now_dt = get_datetime()
		now_date = getdate(now_dt)

		self.posting_date = now_date

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
			and not exists(
				select closed.name
				from `tabPOS Closing Entry Detail` closed
				where closed.document_name = inv.name and document_type = 'Sales Invoice' and closed.docstatus = 1
			)
		""", {
			"pos_profile": self.pos_profile,
			"user": self.user or frappe.session.user,
			"from_date": getdate(self.period_start_date),
			"to_date": getdate(self.period_end_date),
			"company": self.company,
		}, as_dict=1)

	def set_payment_details(self, payment_details):
		self.payment_details = []
		for d in payment_details:
			self.append('payment_details', d)

	def set_summary_values(self, invoices):
		self.total_invoices = len(invoices)

		self.net_total = sum(d.net_total for d in invoices)
		self.grand_total = sum(d.rounded_total or d.grand_total for d in invoices)

		self.total_opening = sum(flt(d.opening_amount) for d in self.payment_reconciliation)
		self.total_collected = sum(flt(d.collected_amount) for d in self.payment_reconciliation)
		self.total_expected = sum(flt(d.expected_amount) for d in self.payment_reconciliation)

	def set_payment_reconciliation(self, payment_summary, pos_opening):
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
		for mop, amount in payment_summary.items():
			row = get_row(mop)
			row.collected_amount = amount

		# Previous Closing Values
		for mode_of_payment, closing_amount in user_closing_amounts.items():
			row = get_row(mode_of_payment)
			row.closing_amount = closing_amount

		# Expected Amount
		for row in self.get('payment_reconciliation'):
			row.expected_amount = flt(row.opening_amount) + flt(row.collected_amount)

	def set_accounts(self):
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

		self.head_cashier_account = frappe.get_cached_value("POS Profile", self.pos_profile, "head_cashier_account")
		self.till_difference_account = frappe.get_cached_value("POS Profile", self.pos_profile, "till_difference_account")

		for d in self.payment_reconciliation:
			d.account = get_bank_cash_account(d.mode_of_payment, self.company, self.pos_profile, override_till_account=False).get("account")
			if not d.account:
				frappe.throw(_("Please configure account for Mode of Payment {0}").format(d.mode_of_payment))

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

	def calculate_totals(self):
		self.total_closing = 0
		self.total_difference = 0
		for d in self.payment_reconciliation:
			d.difference = flt(d.closing_amount) - flt(d.expected_amount)

			self.total_closing += flt(d.closing_amount)
			self.total_difference += d.difference

	def calculate_cash_denominations(self):
		self.total_cash = 0
		for d in self.cash_denominations:
			d.amount = flt(d.denomination) * cint(d.count)
			self.total_cash += d.amount

	def update_pos_opening_entry(self):
		if self.pos_opening_entry:
			doc = frappe.get_doc("POS Opening Entry", self.pos_opening_entry)
			doc.set_status(update=True)
			doc.notify_update()


def get_pos_payment_details(invoices):
	payment_details = []
	payment_summary = {}
	if not invoices:
		return payment_details, payment_summary

	invoice_names = [d.name for d in invoices]

	invoice_payment_data = frappe.db.sql("""
		select
			'Sales Invoice' as document_type,
			inv.name as document_name,
			pay.mode_of_payment,
			pay.reference_no,
			pay.card_type,
			pay.sending_bank,
			pay.receiving_bank,
			pay.base_amount - if(pay.type = 'Cash', inv.base_change_amount, 0) as paid_amount,
			pay.account
		from `tabSales Invoice Payment` pay
		inner join `tabSales Invoice` inv on inv.name = pay.parent
		where inv.name in %s
		order by inv.posting_date, inv.posting_time, inv.creation, pay.idx
	""", [invoice_names], as_dict=1)

	payment_entry_data = frappe.db.sql("""
		select 
			'Payment Entry' as document_type,
			pe.name as document_name,
			pe.mode_of_payment,
			pe.reference_no,
			pe.bank as receiving_bank,
			pe.base_paid_amount as paid_amount,
			pe.paid_to as account
		from `tabPayment Entry Reference` pref
		inner join `tabPayment Entry` pe on pe.name = pref.parent
		where pe.docstatus = 1 and pref.reference_doctype = 'Sales Invoice' and pref.reference_name in %s
		order by pe.posting_date, pe.creation
	""", [invoice_names], as_dict=1)

	journal_entry_data = frappe.db.sql("""
		select
			'Journal Entry' as document_type,
			je.name as document_name,
			je.mode_of_payment,
			jea.cheque_no as reference_no,
			jea.credit - jea.debit as paid_amount,
			jea.account
		from `tabJournal Entry Account` jea
		inner join `tabJournal Entry` je on je.name = jea.parent
		where je.docstatus = 1 and jea.reference_type = 'Sales Invoice' and jea.reference_name in %s
		order by je.posting_date, je.creation, jea.idx
	""", [invoice_names], as_dict=1)

	payment_details = invoice_payment_data + payment_entry_data + journal_entry_data
	for d in payment_details:
		d.mode_of_payment = cstr(d.mode_of_payment)
		d.type = frappe.get_cached_value("Mode of Payment", d.mode_of_payment, "type")

		payment_summary.setdefault(d.mode_of_payment, 0)
		payment_summary[d.mode_of_payment] += d.paid_amount

	payment_details = sorted(payment_details, key=lambda d: list(payment_summary.keys()).index(d.mode_of_payment))

	return payment_details, payment_summary


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

	out = sorted(out, key=lambda d: flt(d.rate), reverse=1)
	return out


@frappe.whitelist()
def make_head_cashier_voucher(pos_closing_entry):
	pce = frappe.get_doc("POS Closing Entry", pos_closing_entry)
	if not pce.head_cashier_account:
		frappe.throw(_("Head Cashier Account not configured"))

	je = make_journal_entry(pce)
	append_debit_accounts(pce, je, override_account=pce.head_cashier_account)
	append_credit_accounts(pce, je)
	append_difference_accounts(pce, je)

	postprocess_journal_entry(je)
	return je


@frappe.whitelist()
def make_till_transfer_voucher(pos_closing_entry):
	pce = frappe.get_doc("POS Closing Entry", pos_closing_entry)

	je = make_journal_entry(pce)
	append_debit_accounts(pce, je)
	append_credit_accounts(pce, je, override_account=pce.head_cashier_account)
	if not pce.head_cashier_account:
		append_difference_accounts(pce, je)

	postprocess_journal_entry(je)
	return je


def make_journal_entry(pce):
	if pce.docstatus != 1:
		frappe.throw(_("POS Closing Entry must be submitted"))

	pos_profile = frappe.get_cached_doc("POS Profile", pce.pos_profile)

	je = frappe.new_doc("Journal Entry")
	je.company = pce.company
	je.branch = pce.branch
	je.user_remark = _("POS Closing Transfer Entry for Cashier {0} POS Profile {1}").format(
		frappe.utils.get_fullname(pce.user), pce.pos_profile
	)
	if pos_profile.cost_center:
		je.cost_center = pos_profile.cost_center

	return je


def append_debit_accounts(pce, je, override_account=None):
	# Debit / Deposit Collections

	mode_accounts = {}
	for d in pce.payment_reconciliation:
		mode_accounts[d.mode_of_payment] = d.account

	cash_row = None
	for d in pce.payment_details:
		if not d.paid_amount:
			continue

		if d.type == "Cash" and cash_row:
			row = cash_row
		else:
			row = je.append("accounts", {
				"account": override_account or mode_accounts.get(d.mode_of_payment),
				"reference_type": "POS Closing Entry",
				"reference_name": pce.name,
				"debit_in_account_currency": 0,
				"cheque_no": d.reference_no if d.type != "Cash" else None,
				"user_remark": _("{0} Collected").format(d.mode_of_payment)
			})

			if d.type == "Cash":
				cash_row = row

		row.debit_in_account_currency += d.paid_amount


def append_credit_accounts(pce, je, override_account=None):
	# Credit / Transfer Collections

	collected_account_totals = {}
	for d in pce.payment_details:
		if d.paid_amount:
			collected_account_totals.setdefault(d.account, 0)
			collected_account_totals[d.account] += d.paid_amount

	for account, amount in collected_account_totals.items():
		je.append("accounts", {
			"account": override_account or account,
			"reference_type": "POS Closing Entry",
			"reference_name": pce.name,
			"credit_in_account_currency": abs(amount) if amount > 0 else 0,
			"debit_in_account_currency": abs(amount) if amount < 0 else 0,
			"user_remark": _("Till Balance Transfer")
		})


def append_difference_accounts(pce, je):
	# Difference Expense
	if pce.total_difference:
		je.append("accounts", {
			"account": pce.till_difference_account,
			"reference_type": "POS Closing Entry",
			"reference_name": pce.name,
			"debit_in_account_currency": abs(pce.total_difference) if pce.total_difference < 0 else 0,
			"credit_in_account_currency": abs(pce.total_difference) if pce.total_difference > 0 else 0,
			"user_remark": _("Total Till Difference"),
		})

	# Difference Reconciliation
	for d in pce.payment_reconciliation:
		if not d.difference:
			continue

		je.append("accounts", {
			"account": d.account,
			"reference_type": "POS Closing Entry",
			"reference_name": pce.name,
			"debit_in_account_currency": abs(d.difference) if d.difference > 0 else 0,
			"credit_in_account_currency": abs(d.difference) if d.difference < 0 else 0,
			"user_remark": _("{0} Difference Reconciliation").format(d.mode_of_payment),
		})


def postprocess_journal_entry(je):
	je.set_exchange_rate()
	je.set_amounts_in_company_currency()
	je.set_total_debit_credit()
	je.set_party_name()

	return je
