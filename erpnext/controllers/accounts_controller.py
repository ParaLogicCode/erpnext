# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
import erpnext
import json
from frappe import _, scrub
from frappe.utils import (
	today, flt, formatdate, cstr, date_diff, getdate, nowdate, get_link_to_form,
	clean_whitespace, cint,
)
from frappe.model.workflow import get_workflow_name, is_transition_condition_satisfied
from erpnext.stock.get_item_details import get_conversion_factor
from erpnext.accounts.utils import get_fiscal_years, validate_fiscal_year, get_account_currency
from erpnext.utilities.transaction_base import TransactionBase
from erpnext.buying.utils import update_last_purchase_rate
from erpnext.accounts.party import get_party_account_currency, validate_party_frozen_disabled
from erpnext.exceptions import InvalidCurrency
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
from erpnext.stock.get_item_details import get_default_warehouse
from erpnext.stock.doctype.packed_item.packed_item import make_packing_list
from erpnext.accounts.doctype.payment_terms_template.payment_terms_template import get_payment_term_due_date, \
	get_payment_terms, get_due_date_from_template
from collections import OrderedDict


class AccountsController(TransactionBase):
	def __init__(self, *args, **kwargs):
		super(AccountsController, self).__init__(*args, **kwargs)

	@property
	def company_currency(self):
		if not hasattr(self, "__company_currency"):
			self.__company_currency = erpnext.get_company_currency(self.company)

		return self.__company_currency

	@property
	def company_abbr(self):
		if not hasattr(self, "_abbr"):
			self._abbr = frappe.db.get_value('Company',  self.company,  "abbr")

		return self._abbr

	def onload(self):
		self.set_onload("make_payment_via_journal_entry",
			frappe.db.get_single_value('Accounts Settings', 'make_payment_via_journal_entry'))

		if self.docstatus == 0:
			self.set_missing_values(for_validate=True)

	def before_print(self, print_settings=None):
		self.company_address_doc = erpnext.get_company_address_doc(self)

		if self.doctype in ['Journal Entry', 'Payment Entry']:
			self.get_gl_entries_for_print()
			self.get_party_to_party_name_dict()

	def validate(self):
		if self.get("_action") and self._action != "update_after_submit":
			self.set_missing_values(for_validate=True)

		self.validate_date_with_fiscal_year()
		self.validate_party()
		self.ensure_supplier_is_not_blocked()
		self.validate_currency()
		self.clean_remarks()

		validate_regional(self)

	def set_missing_values(self, for_validate=False):
		if frappe.flags.in_test:
			for fieldname in ["posting_date", "transaction_date"]:
				if self.meta.get_field(fieldname) and not self.get(fieldname):
					self.set(fieldname, today())
					break

	def validate_date_with_fiscal_year(self):
		if self.meta.get_field("fiscal_year"):
			date_field = ""
			if self.meta.get_field("posting_date"):
				date_field = "posting_date"
			elif self.meta.get_field("transaction_date"):
				date_field = "transaction_date"

			if date_field and self.get(date_field):
				validate_fiscal_year(self.get(date_field), self.fiscal_year, self.company, self.meta.get_label(date_field), self)

	def clean_remarks(self):
		fields = [
			'remarks', 'remark', 'user_remark', 'user_remarks',
			'cheque_no', 'reference_no',
			'po_no', 'supplier_delivery_note', 'lr_no'
		]
		for f in fields:
			if self.meta.has_field(f):
				self.set(f, clean_whitespace(self.get(f)))

	def validate_party(self):
		party_type, party, party_name = self.get_party()
		validate_party_frozen_disabled(party_type, party)

		billing_party_type, billing_party, party_name = self.get_billing_party()
		if (billing_party_type, billing_party) != (party_type, party):
			validate_party_frozen_disabled(billing_party_type, billing_party)

	def get_party(self):
		if self.meta.has_field("party_type") and self.meta.has_field("party"):
			return self.get("party_type"), self.get("party"), self.get("party_name")
		else:
			party_type = None
			if self.meta.get_field("customer"):
				party_type = "Customer"
			elif self.meta.get_field("supplier"):
				party_type = "Supplier"

			party = self.get(scrub(party_type)) if party_type else None
			party_name = self.get(scrub(party_type) + "_name") if party else None
			return party_type, party, party_name

	def get_billing_party(self):
		return self.get_party()

	def ensure_supplier_is_not_blocked(self, is_payment=False, supplier=None):
		if not supplier:
			if self.get("party") and self.get("party_type") == "Supplier":
				supplier = self.get("party")
			else:
				supplier = self.get("supplier")

		if not supplier:
			return

		supplier_doc = frappe.get_cached_doc("Supplier", supplier)
		if supplier_doc.on_hold and (
			(not is_payment and supplier_doc.hold_type in ['All', 'Invoices'])
			or (is_payment and supplier_doc.hold_type in ['All', 'Payments'])
		):
			if not supplier_doc.release_date or getdate(nowdate()) <= getdate(supplier_doc.release_date):
				frappe.throw(_("{0} is blocked so this transaction cannot proceed").format(
					frappe.get_desk_link("Supplier", supplier)
				))

	def validate_currency(self):
		if not self.get("currency"):
			return

		party_type, party, party_name = self.get_billing_party()
		if party_type and party:
			party_account_currency = get_party_account_currency(party_type, party, self.company)

			if (
				party_account_currency
				and party_account_currency != self.company_currency
				and self.currency != party_account_currency
			):
				frappe.throw(_("Accounting Entry for {0}: {1} can only be made in currency: {2}").format(
					party_type, party, party_account_currency
				), InvalidCurrency)

	def validate_payment_schedule(self):
		if not self.meta.has_field("payment_schedule"):
			return

		self.set_payment_schedule()
		self.validate_payment_schedule_dates()
		self.validate_payment_schedule_amount()

		if self.meta.has_field("due_date"):
			self.set_due_date()
			self.validate_due_date()

	def set_payment_schedule(self, exclude_bill_date=False):
		if self.get("is_pos") or self.get("is_return"):
			self.payment_terms_template = None
			self.payment_schedule = []
			return

		posting_date = self.get("posting_date") or self.get("transaction_date")
		bill_date = self.get("bill_date") if not exclude_bill_date else None
		due_date = self.get("due_date") or posting_date
		delivery_date = self.get("delivery_date") or self.get("schedule_date")

		payable_amount = self.get_payable_amount()
		remaining_amount = payable_amount

		if not self.get("payment_schedule"):
			if self.get("payment_terms_template"):
				data = get_payment_terms(self.payment_terms_template, posting_date=posting_date,
					delivery_date=delivery_date, bill_date=bill_date, grand_total=payable_amount)
				for item in data:
					self.append("payment_schedule", item)
			else:
				self.append("payment_schedule", {
					"due_date": due_date,
					"invoice_portion": 100,
					"payment_amount": payable_amount,
					"payment_amount_type": "Percentage"
				})
		else:
			for d in self.get("payment_schedule"):
				if d.payment_term:
					term = frappe.get_cached_doc("Payment Term", d.payment_term)
					d.due_date = get_payment_term_due_date(term, posting_date, bill_date=bill_date, delivery_date=delivery_date)

				if getdate(d.due_date) < getdate(posting_date):
					d.due_date = posting_date

		for d in self.get("payment_schedule"):
			if d.payment_amount_type == "Remaining Amount":
				d.payment_amount = flt(remaining_amount, d.precision('payment_amount'))
			elif d.payment_amount_type == "Amount":
				term_payment_amount = frappe.get_cached_value("Payment Term", d.payment_term, "payment_amount")\
					if d.payment_term else 0
				payment_amount = flt(term_payment_amount or d.payment_amount)
				d.payment_amount = flt(min(payment_amount, payable_amount), d.precision('payment_amount'))
			else:
				d.payment_amount = flt(payable_amount * flt(d.invoice_portion) / 100, d.precision('payment_amount'))

			remaining_amount -= d.payment_amount

			if d.payment_amount_type in ("Amount", "Remaining Amount"):
				d.invoice_portion = flt(d.payment_amount / payable_amount * 100) if payable_amount else 0

	def validate_payment_schedule_dates(self):
		if self.get("is_pos") or self.get("is_return"):
			return

		dates = []
		li = []

		for d in self.get("payment_schedule"):
			if self.get("transaction_date") and getdate(d.due_date) < getdate(self.transaction_date):
				frappe.throw(_("Row {0}: Due Date in the Payment Terms table cannot be before Transaction Date").format(d.idx))
			if d.due_date in dates:
				li.append(_("{0} in row {1}").format(frappe.format(getdate(d.due_date)), d.idx))
			dates.append(d.due_date)

		if li:
			duplicates = '<br>' + '<br>'.join(li)
			frappe.msgprint(_("Payment Schedule rows with duplicate Due Dates found: {0}").format(duplicates),
				alert=True, indicator='orange')

	def validate_payment_schedule_amount(self):
		if self.get("is_pos") or self.get("is_return"):
			return

		if self.get("payment_schedule"):
			payment_schedule_precision = self.precision("payment_amount", "payment_schedule")

			payment_schedule_total = sum([d.payment_amount for d in self.get("payment_schedule")])
			payment_schedule_total = flt(payment_schedule_total, payment_schedule_precision)

			payable_amount = self.get_payable_amount()
			payable_amount = flt(payable_amount, payment_schedule_precision)

			if payment_schedule_total != payable_amount:
				frappe.throw(_("Total Payment Amount in Payment Schedule must be equal to Grand / Rounded Total"))

	def get_payable_amount(self):
		grand_total = flt(self.get("rounded_total") or self.get("grand_total"))

		if self.get("write_off_amount"):
			grand_total -= flt(self.write_off_amount)

		if self.get("total_advance"):
			grand_total -= flt(self.get("total_advance"))

		return grand_total

	def set_due_date(self):
		due_dates = [getdate(d.due_date) for d in self.get("payment_schedule") if d.due_date]
		if due_dates:
			self.due_date = max(due_dates)

	def validate_due_date(self, exclude_bill_date=False):
		if self.get('is_pos'):
			return
		if not self.meta.has_field("due_date"):
			return

		posting_date = self.get("posting_date") or self.get("transaction_date")
		bill_date = self.get("bill_date") if not exclude_bill_date else None
		delivery_date = self.get("delivery_date") or self.get("schedule_date")

		if not self.due_date:
			frappe.throw(_("Due Date is mandatory"))
		if getdate(self.due_date) < getdate(bill_date or posting_date):
			frappe.throw(_("Due Date cannot be before Posting / Supplier Invoice Date"))

		if self.get("payment_terms_template"):
			default_due_date = get_due_date_from_template(self.payment_terms_template, posting_date=posting_date,
				bill_date=bill_date, delivery_date=delivery_date)
			if not default_due_date:
				return

			if default_due_date != posting_date and getdate(self.due_date) > getdate(default_due_date):
				is_credit_controller = frappe.db.get_single_value("Accounts Settings", "credit_controller") in frappe.get_roles()
				if is_credit_controller:
					frappe.msgprint(_("Note: Due Date exceeds allowed customer credit days by {0} day(s)").format(
						date_diff(self.due_date, default_due_date)
					))
				else:
					frappe.throw(_("Due Date cannot be after {0} for Payment Terms Template {1}").format(
						formatdate(default_due_date), self.payment_terms_template
					))

	def get_gl_dict(self, args, account_currency=None, item=None):
		"""this method populates the common properties of a gl entry record"""

		posting_date = args.get('posting_date') or self.get('posting_date')
		fiscal_years = get_fiscal_years(posting_date, company=self.company)
		if len(fiscal_years) > 1:
			frappe.throw(_("Multiple fiscal years exist for the date {0}. Please set company in Fiscal Year").format(
				formatdate(posting_date)))
		else:
			fiscal_year = fiscal_years[0][0]

		gl_dict = frappe._dict({
			'company': self.company,
			'posting_date': posting_date,
			'fiscal_year': fiscal_year,
			'voucher_type': self.doctype,
			'voucher_no': self.name,
			'remarks': self.get("remarks") or self.get("remark"),
			'debit': 0,
			'credit': 0,
			'debit_in_account_currency': 0,
			'credit_in_account_currency': 0,
			'is_opening': self.get("is_opening") or "No",
			'party_type': None,
			'party': None,
			'project': item and item.get("project") or self.get("project"),
			'cost_center': item and item.get("cost_center") or self.get("cost_center"),
			'reference_no': self.get("reference_no") or self.get("cheque_no") or self.get("bill_no"),
			'reference_date': self.get("reference_date") or self.get("cheque_date") or self.get("bill_date")
		})

		accounting_dimensions = get_accounting_dimensions(as_list=False)
		dimension_dict = frappe._dict()

		for dimension in accounting_dimensions:
			dimension_dict[dimension.fieldname] = self.get(dimension.fieldname)
			if item and item.get(dimension.fieldname):
				dimension_dict[dimension.fieldname] = item.get(dimension.fieldname)

			if not args.get(dimension.fieldname) and args.get('party') and args.get('party_type') == dimension.document_type:
				dimension_dict[dimension.fieldname] = args.get('party')

		gl_dict.update(dimension_dict)
		gl_dict.update(args)

		if not account_currency:
			account_currency = get_account_currency(gl_dict.account)

		if gl_dict.account and self.doctype not in ["Journal Entry", "Period Closing Voucher", "Payment Entry"]:
			self.validate_account_currency(gl_dict.account, account_currency)
			set_balance_in_account_currency(gl_dict, account_currency, self.get("conversion_rate"), self.company_currency)

		return gl_dict

	def validate_account_currency(self, account, account_currency=None):
		valid_currency = [self.company_currency]
		if self.get("currency") and self.currency != self.company_currency:
			valid_currency.append(self.currency)

		if account_currency not in valid_currency:
			frappe.throw(_("Account {0} is invalid. Account Currency must be {1}").format(
				account, _(" or ").join(valid_currency)
			))

	def unlink_payments_on_invoice_cancel(self):
		from erpnext.accounts.utils import unlink_ref_doc_from_payment_entries
		if not self.get("is_return"):
			unlink_ref_doc_from_payment_entries(self, True)

	def unlink_payments_on_order_cancel(self):
		from erpnext.accounts.utils import unlink_ref_doc_from_payment_entries
		if frappe.db.get_single_value('Accounts Settings', 'unlink_advance_payment_on_cancelation_of_order'):
			unlink_ref_doc_from_payment_entries(self, True)

	@frappe.whitelist()
	def set_advances(self, include_unallocated=True):
		"""Returns list of advances against Account, Party, Reference"""
		include_unallocated = cint(include_unallocated)
		res = self.get_advance_entries(include_unallocated=include_unallocated)
		company_currency = erpnext.get_company_currency(self.company)

		self.set("advances", [])

		advance_allocated = 0
		if self.get("party_account_currency") and self.get("party_account_currency") == company_currency:
			grand_total = self.get("base_rounded_total") or self.get("base_grand_total")
		else:
			grand_total = self.get("rounded_total") or self.get("grand_total")

		for d in res:
			remaining_amount = flt(grand_total) - advance_allocated
			allocated_amount = flt(min(remaining_amount, flt(d.amount)), self.precision('total_advance'))

			advance_allocated += flt(allocated_amount)

			self.append("advances", {
				"doctype": self.doctype + " Advance",
				"reference_type": d.reference_type,
				"reference_name": d.reference_name,
				"reference_row": d.reference_row,
				"remarks": d.remarks,
				"advance_amount": flt(d.amount),
				"allocated_amount": allocated_amount
			})

	def clear_unallocated_advances(self, parentfield="advances"):
		self.set(parentfield, self.get(parentfield, {"allocated_amount": ["not in", [0, None, ""]]}))
		for i, d in enumerate(self.get(parentfield)):
			d.idx = i + 1

	def get_advance_entries(self, include_unallocated=True):
		against_all_orders = False
		order_field = None
		order_doctype = None
		if self.doctype == "Sales Invoice":
			party_account = self.debit_to
			party_type, party, party_name = self.get_billing_party()
			order_field = "sales_order"
			order_doctype = "Sales Order"
		elif self.doctype == "Purchase Invoice":
			party_account = self.credit_to
			party_type, party, party_name = self.get_billing_party()
			order_field = "purchase_order"
			order_doctype = "Purchase Order"
		elif self.doctype == "Expense Claim":
			party_account = self.payable_account
			party_type = "Employee"
			party = self.employee
		else:
			party_account = self.credit_to
			party_type = self.party_type
			party = self.party

		if order_field:
			order_list = list(set([d.get(order_field) for d in self.get("items") if d.get(order_field)]))
		else:
			order_list = []

		journal_entries = get_advance_journal_entries(party_type, party, party_account,
			order_doctype, order_list, include_unallocated, against_all_orders=against_all_orders)

		payment_entries = get_advance_payment_entries(party_type, party, party_account,
			order_doctype, order_list, include_unallocated, against_all_orders=against_all_orders)

		res = sorted(journal_entries + payment_entries, key=lambda d: (not bool(d.against_order), d.posting_date))

		return res

	def check_advance_payment_against_order(self, order_field):
		if self.get("is_return"):
			return

		order_list = list(set([d.get(order_field) for d in self.get("items") if d.get(order_field)]))
		if not order_list:
			return

		advance_entries = self.get_advance_entries(include_unallocated=False)
		if advance_entries:
			advance_entries_against_si = [d.reference_name for d in self.get("advances")]
			for d in advance_entries:
				if not advance_entries_against_si or d.reference_name not in advance_entries_against_si:
					frappe.msgprint(_(
						"Payment Entry {0} is linked against Order {1}, check if it should be pulled as advance in this invoice.")
							.format(d.reference_name, d.against_order))

	def update_against_document_in_jv(self):
		"""
			Links invoice and advance voucher:
				1. cancel advance voucher
				2. split into multiple rows if partially adjusted, assign against voucher
				3. submit advance voucher
		"""

		if self.doctype == "Sales Invoice":
			party_type, party, party_name = self.get_billing_party()
			party_account = self.debit_to
			dr_or_cr = "credit_in_account_currency"
		elif self.doctype == "Purchase Invoice":
			party_type, party, party_name = self.get_billing_party()
			party_account = self.credit_to
			dr_or_cr = "debit_in_account_currency"
		elif self.doctype == "Expense Claim":
			party_type, party, party_name = self.get_billing_party()
			party_account = self.payable_account
			dr_or_cr = "debit_in_account_currency"
		else:
			party_type = self.party_type
			party = self.party
			party_account = self.credit_to
			dr_or_cr = "debit_in_account_currency"

		if self.doctype in ["Sales Invoice", "Purchase Invoice"]:
			invoice_amounts = {
				'exchange_rate': (self.conversion_rate if self.party_account_currency != self.company_currency else 1),
				'grand_total': (self.base_grand_total if self.party_account_currency == self.company_currency else self.grand_total)
			}
		elif self.doctype == "Expense Claim":
			invoice_amounts = {
				'exchange_rate': 1,
				'grand_total': self.total_sanctioned_amount
			}
		else:
			invoice_amounts = {
				'exchange_rate': 1,
				'grand_total': self.grand_total
			}

		lst = []
		for d in self.get('advances'):
			if flt(d.allocated_amount) > 0 and d.reference_type != 'Employee Advance':
				args = frappe._dict({
					'voucher_type': d.reference_type,
					'voucher_no': d.reference_name,
					'voucher_detail_no': d.reference_row,
					'against_voucher_type': self.doctype,
					'against_voucher': self.name,
					'account': party_account,
					'party_type': party_type,
					'party': party,
					'dr_or_cr': dr_or_cr,
					'unadjusted_amount': flt(d.advance_amount),
					'allocated_amount': flt(d.allocated_amount),
					'outstanding_amount': self.outstanding_amount
				})
				args.update(invoice_amounts)
				lst.append(args)

		if lst:
			from erpnext.accounts.utils import reconcile_against_document
			reconcile_against_document(lst)

	def get_company_default(self, fieldname):
		from erpnext.accounts.utils import get_company_default
		return get_company_default(self.company, fieldname)

	def delink_advance_entries(self, linked_doc_name):
		total_allocated_amount = 0
		for adv in self.advances:
			consider_for_total_advance = True
			if adv.reference_name == linked_doc_name:
				frappe.db.sql("""delete from `tab{0} Advance`
					where name = %s""".format(self.doctype), adv.name)
				consider_for_total_advance = False

			if consider_for_total_advance:
				total_allocated_amount += flt(adv.allocated_amount, adv.precision("allocated_amount"))

		frappe.db.set_value(self.doctype, self.name, "total_advance", total_allocated_amount, update_modified=False)

	def get_gl_entries_for_print(self):
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
		dimension_fields = get_accounting_dimensions()

		if self.docstatus == 1:
			gles = frappe.get_all(
				"GL Entry",
				filters={
					"voucher_type": self.doctype, "voucher_no": self.name
				},
				fields=[
					'account', 'remarks', 'party_type', 'party', 'debit', 'credit',
					'against_voucher', 'against_voucher_type', 'reference_no', 'reference_date'
				] + dimension_fields,
				order_by="creation"
			)
		else:
			gles = self.get_gl_entries()

		grouped_gles = OrderedDict()

		for gle in gles:
			key = [gle.account, cstr(gle.party_type), cstr(gle.party), cstr(gle.remarks), cstr(gle.reference_no),
				cstr(gle.reference_date), bool(gle.against_voucher)]
			key += [cstr(gle.get(f)) for f in dimension_fields]
			key = tuple(key)
			group = grouped_gles.setdefault(key, frappe._dict({
				"account": cstr(gle.account),
				"party_type": cstr(gle.party_type),
				"party": cstr(gle.party),
				"remarks": cstr(gle.remarks),
				"reference_no": cstr(gle.reference_no),
				"reference_date": cstr(gle.reference_date),
				"sum": 0, "against_voucher_set": set(), "against_voucher": []
			}))
			for f in dimension_fields:
				group[f] = cstr(gle.get(f))
			group.sum += flt(gle.debit) - flt(gle.credit)
			if gle.against_voucher_type and gle.against_voucher:
				group.against_voucher_set.add((cstr(gle.against_voucher_type), cstr(gle.against_voucher)))

		for d in grouped_gles.values():
			d.debit = d.sum if d.sum > 0 else 0
			d.credit = -d.sum if d.sum < 0 else 0

			for against_voucher_type, against_voucher in d.against_voucher_set:
				bill_no = None
				if against_voucher_type in ['Journal Entry', 'Purchase Invoice']:
					bill_no = frappe.db.get_value(against_voucher_type, against_voucher, 'bill_no')

				if bill_no:
					d.against_voucher.append(bill_no)
				else:
					d.against_voucher.append(frappe.utils.get_original_name(against_voucher_type, against_voucher))

			d.against_voucher = ", ".join(d.against_voucher or [])

		self.gl_entries = list(grouped_gles.values())
		self.total_debit = sum([d.debit for d in self.gl_entries])
		self.total_credit = sum([d.credit for d in self.gl_entries])

	def get_party_to_party_name_dict(self):
		self.party_to_party_name = {}
		if self.doctype == "Payment Entry":
			self.party_to_party_name[(self.party_type, self.party)] = self.party_name
		if self.doctype == "Journal Entry":
			for d in self.accounts:
				if d.party_type and d.party and d.party_name:
					self.party_to_party_name[(d.party_type, d.party)] = d.party_name

	def validate_deferred_start_and_end_date(self):
		if self.get("is_return"):
			return

		for d in self.items:
			if d.get("enable_deferred_revenue") or d.get("enable_deferred_expense"):
				if not (d.service_start_date and d.service_end_date):
					frappe.throw(_("Row #{0}: Service Start and End Date is required for deferred accounting").format(d.idx))
				elif getdate(d.service_start_date) > getdate(d.service_end_date):
					frappe.throw(_("Row #{0}: Service Start Date cannot be greater than Service End Date").format(d.idx))
				elif getdate(self.posting_date) > getdate(d.service_end_date):
					frappe.throw(_("Row #{0}: Service End Date cannot be before Invoice Posting Date").format(d.idx))


def validate_conversion_rate(currency, conversion_rate, conversion_rate_label, company):
	"""common validation for currency and price list currency"""

	company_currency = frappe.get_cached_value('Company',  company,  "default_currency")

	if not conversion_rate:
		frappe.throw(_("{0} is mandatory. Maybe Currency Exchange record is not created for {1} to {2}.").format(
			conversion_rate_label, currency, company_currency))


def set_balance_in_account_currency(gl_dict, account_currency=None, conversion_rate=None, company_currency=None):
	if (not conversion_rate) and (account_currency != company_currency):
		frappe.throw(_("Account: {0} with currency: {1} can not be selected").format(gl_dict.account, account_currency))

	gl_dict["account_currency"] = company_currency if account_currency == company_currency else account_currency

	# set debit/credit in account currency if not provided
	if flt(gl_dict.debit) and not flt(gl_dict.debit_in_account_currency):
		gl_dict.debit_in_account_currency = gl_dict.debit if account_currency == company_currency \
			else flt(gl_dict.debit / conversion_rate, 2)

	if flt(gl_dict.credit) and not flt(gl_dict.credit_in_account_currency):
		gl_dict.credit_in_account_currency = gl_dict.credit if account_currency == company_currency \
			else flt(gl_dict.credit / conversion_rate, 2)


def get_advance_journal_entries(party_type, party, party_account, order_doctype,
		order_list=None, include_unallocated=True, against_all_orders=False, against_account=None, limit=None):
	journal_entries = []
	if erpnext.get_party_account_type(party_type) == "Receivable":
		dr_or_cr = "credit_in_account_currency"
		bal_dr_or_cr = "gle_je.credit_in_account_currency - gle_je.debit_in_account_currency"
		payment_dr_or_cr = "gle_payment.debit_in_account_currency - gle_payment.credit_in_account_currency"
	else:
		dr_or_cr = "debit_in_account_currency"
		bal_dr_or_cr = "gle_je.debit_in_account_currency - gle_je.credit_in_account_currency"
		payment_dr_or_cr = "gle_payment.credit_in_account_currency - gle_payment.debit_in_account_currency"

	limit_cond = "limit %(limit)s" if limit else ""

	# JVs against order documents
	if order_list or against_all_orders:
		if order_list:
			order_condition = "and ifnull(jea.reference_name, '') in ({0})" \
				.format(", ".join([frappe.db.escape(d) for d in order_list]))
		else:
			order_condition = "and ifnull(jea.reference_name, '') != ''"

		against_account_condition = "and jea.against_account like '%%{0}%%'".format(frappe.db.escape(against_account)) \
			if against_account else ""

		journal_entries += frappe.db.sql("""
			select
				'Journal Entry' as reference_type, je.name as reference_name, je.remark as remarks,
				jea.{dr_or_cr} as amount, jea.name as reference_row, jea.reference_name as against_order,
				je.posting_date
			from
				`tabJournal Entry` je, `tabJournal Entry Account` jea
			where
				je.name = jea.parent and jea.account = %(account)s
				and jea.party_type = %(party_type)s and jea.party = %(party)s
				and {dr_or_cr} > 0 and jea.reference_type = '{order_doctype}' and je.docstatus = 1
				{order_condition} {against_account_condition}
			order by je.posting_date
			{limit_cond}""".format(
				dr_or_cr=dr_or_cr,
				order_doctype=order_doctype,
				order_condition=order_condition,
				against_account_condition=against_account_condition,
				limit_cond=limit_cond
			), {
			"party_type": party_type,
			"party": party,
			"account": party_account,
			"limit": limit
			}, as_dict=1)

	# Unallocated payment JVs
	if include_unallocated:
		against_account_condition = ""
		if against_account:
			against_account_condition = "and GROUP_CONCAT(gle_je.against) like '%%{0}%%'".format(frappe.db.escape(against_account))

		journal_entries += frappe.db.sql("""
		select
			gle_je.voucher_type as reference_type, je.name as reference_name, je.remark as remarks, je.posting_date,
			ifnull(sum({bal_dr_or_cr}), 0) - (
				select ifnull(sum({payment_dr_or_cr}), 0)
				from `tabGL Entry` gle_payment
				where
					gle_payment.against_voucher_type = gle_je.voucher_type
					and gle_payment.against_voucher = gle_je.voucher_no
					and gle_payment.party_type = gle_je.party_type
					and gle_payment.party = gle_je.party
					and gle_payment.account = gle_je.account
					and abs({payment_dr_or_cr}) > 0
			) as amount
		from `tabGL Entry` gle_je
		inner join `tabJournal Entry` je on je.name = gle_je.voucher_no
		where
			gle_je.party_type = %(party_type)s and gle_je.party = %(party)s and gle_je.account = %(account)s
			and gle_je.voucher_type = 'Journal Entry' and (gle_je.against_voucher = '' or gle_je.against_voucher is null)
			and abs({bal_dr_or_cr}) > 0
		group by gle_je.voucher_no
		having amount > 0.005 {against_account_condition}
		order by gle_je.posting_date
		{limit_cond}""".format(
			bal_dr_or_cr=bal_dr_or_cr,
			payment_dr_or_cr=payment_dr_or_cr,
			against_account_condition=against_account_condition,
			limit_cond=limit_cond
		), {
			"party_type": party_type,
			"party": party,
			"account": party_account,
			"limit": limit
		}, as_dict=True)

	return list(journal_entries)


def get_advance_payment_entries(party_type, party, party_account, order_doctype,
		order_list=None, include_unallocated=True, against_all_orders=False, against_account=None, limit=None):
	payment_entries_against_order, unallocated_payment_entries = [], []
	party_account_type = erpnext.get_party_account_type(party_type)
	party_account_field = "paid_from" if party_account_type == "Receivable" else "paid_to"
	against_account_field = "paid_to" if party_account_type == "Receivable" else "paid_from"
	payment_type = "Receive" if party_account_type == "Receivable" else "Pay"
	limit_cond = "limit %s" % limit if limit else ""

	against_account_condition = ""
	if against_account:
		against_account_condition = "and pe.{against_account_field} = {against_account}".format(
			against_account_field=against_account_field, against_account=frappe.db.escape(against_account))

	if order_list or against_all_orders:
		if order_list:
			reference_condition = " and pref.reference_name in ({0})" \
				.format(', '.join(['%s'] * len(order_list)))
		else:
			reference_condition = ""
			order_list = []

		payment_entries_against_order = frappe.db.sql("""
			select
				'Payment Entry' as reference_type, pe.name as reference_name,
				pe.remarks, pref.allocated_amount as amount, pref.name as reference_row,
				pref.reference_name as against_order, pe.posting_date
			from `tabPayment Entry` pe, `tabPayment Entry Reference` pref
			where
				pe.name = pref.parent and pe.{party_account_field} = %s and pe.payment_type = %s
				and pe.party_type = %s and pe.party = %s and pe.docstatus = 1
				and pref.reference_doctype = %s
				{reference_condition} {against_account_condition}
			order by pe.posting_date
			{limit_cond}
		""".format(
			party_account_field=party_account_field,
			reference_condition=reference_condition,
			against_account_condition=against_account_condition,
			limit_cond=limit_cond
		), [party_account, payment_type, party_type, party, order_doctype] + order_list, as_dict=1)

	if include_unallocated:
		unallocated_payment_entries = frappe.db.sql("""
			select 'Payment Entry' as reference_type, name as reference_name, remarks, unallocated_amount as amount,
				pe.posting_date
			from `tabPayment Entry` pe
			where
				{party_account_field} = %s and party_type = %s and party = %s and payment_type = %s
				and docstatus = 1 and unallocated_amount > 0
				{against_account_condition}
			order by posting_date
			{limit_cond}
		""".format(
			party_account_field=party_account_field,
			against_account_condition=against_account_condition,
			limit_cond=limit_cond
		), [party_account, party_type, party, payment_type], as_dict=1)

	return list(payment_entries_against_order) + list(unallocated_payment_entries)


def get_supplier_block_status(party_name):
	"""
	Returns a dict containing the values of `on_hold`, `release_date` and `hold_type` of
	a `Supplier`
	"""
	supplier = frappe.get_doc('Supplier', party_name)
	info = {
		'on_hold': supplier.on_hold,
		'release_date': supplier.release_date,
		'hold_type': supplier.hold_type
	}
	return info


def set_sales_order_defaults(parent_doctype, parent_doctype_name, child_docname, trans_item):
	"""
	Returns a Sales Order Item child item containing the default values
	"""
	p_doc = frappe.get_doc(parent_doctype, parent_doctype_name)
	child_item = frappe.new_doc('Sales Order Item', p_doc, child_docname)
	item = frappe.get_cached_doc("Item", trans_item.get('item_code'))
	child_item.item_code = item.item_code
	child_item.item_name = item.item_name
	child_item.description = item.description
	child_item.delivery_date = trans_item.get('delivery_date') or p_doc.delivery_date
	child_item.conversion_factor = flt(trans_item.get('conversion_factor')) or get_conversion_factor(item.item_code, item.stock_uom).get("conversion_factor") or 1.0
	child_item.uom = item.stock_uom
	child_item.stock_uom = item.stock_uom
	child_item.is_stock_item = item.is_stock_item

	if p_doc.get('set_warehouse'):
		child_item.warehouse = p_doc.get('set_warehouse')
	else:
		warehouse_args = p_doc.as_dict()
		warehouse_args.transaction_type_name = warehouse_args.get('transaction_type')
		child_item.warehouse = get_default_warehouse(item, p_doc)

	if not child_item.warehouse:
		frappe.throw(_("Cannot find {0} for item {1}. Please set the same in Item Master or Stock Settings.")
			.format(frappe.bold("default warehouse"), frappe.bold(item.item_code)))

	return child_item


def set_purchase_order_defaults(parent_doctype, parent_doctype_name, child_docname, trans_item):
	"""
	Returns a Purchase Order Item child item containing the default values
	"""
	p_doc = frappe.get_doc(parent_doctype, parent_doctype_name)
	child_item = frappe.new_doc('Purchase Order Item', p_doc, child_docname)
	item = frappe.get_cached_doc("Item", trans_item.get('item_code'))
	child_item.item_code = item.item_code
	child_item.item_name = item.item_name
	child_item.description = item.description
	child_item.schedule_date = trans_item.get('schedule_date') or p_doc.schedule_date
	child_item.conversion_factor = flt(trans_item.get('conversion_factor')) or get_conversion_factor(item.item_code, item.stock_uom).get("conversion_factor") or 1.0
	child_item.uom = item.stock_uom
	child_item.stock_uom = item.stock_uom
	child_item.base_rate = 1 # Initiallize value will update in parent validation
	child_item.base_amount = 1 # Initiallize value will update in parent validation
	child_item.is_stock_item = item.is_stock_item
	return child_item


def validate_and_delete_children(parent, data):
	deleted_children = []
	updated_item_names = [d.get("docname") for d in data]
	for item in parent.items:
		if item.name not in updated_item_names:
			deleted_children.append(item)

	for d in deleted_children:
		if parent.doctype == "Sales Order":
			if flt(d.delivered_qty):
				frappe.throw(_("Row #{0}: Cannot delete item {1} which has already been delivered").format(d.idx, d.item_code))
			if flt(d.work_order_qty):
				frappe.throw(_("Row #{0}: Cannot delete item {1} which has work order assigned to it.").format(d.idx, d.item_code))
			if flt(d.ordered_qty):
				frappe.throw(_("Row #{0}: Cannot delete item {1} which is assigned to customer's purchase order.").format(d.idx, d.item_code))

		if parent.doctype == "Purchase Order" and flt(d.received_qty):
			frappe.throw(_("Row #{0}: Cannot delete item {1} which has already been received").format(d.idx, d.item_code))

		if flt(d.billed_amt):
			frappe.throw(_("Row #{0}: Cannot delete item {1} which has already been billed.").format(d.idx, d.item_code))

		d.cancel()
		d.delete()


@frappe.whitelist()
def update_child_qty_rate(parent_doctype, trans_items, parent_doctype_name, child_docname="items"):
	def check_doc_permissions(doc, perm_type='create'):
		try:
			doc.check_permission(perm_type)
		except frappe.PermissionError:
			actions = { 'create': 'add', 'write': 'update', 'cancel': 'remove' }

			frappe.throw(_("You do not have permissions to {} items in a {}.")
				.format(actions[perm_type], parent_doctype), title=_("Insufficient Permissions"))
	
	def validate_workflow_conditions(doc):
		workflow = get_workflow_name(doc.doctype)
		if not workflow:
			return

		workflow_doc = frappe.get_doc("Workflow", workflow)
		current_state = doc.get(workflow_doc.workflow_state_field)
		roles = frappe.get_roles()

		transitions = []
		for transition in workflow_doc.transitions:
			if transition.next_state == current_state and transition.allowed in roles:
				if not is_transition_condition_satisfied(transition, doc):
					continue
				transitions.append(transition.as_dict())

		if not transitions:
			frappe.throw(
				_("You are not allowed to update as per the conditions set in {} Workflow.").format(get_link_to_form("Workflow", workflow)),
				title=_("Insufficient Permissions")
			)

	def get_new_child_item(item_row):
		new_child_function = set_sales_order_defaults if parent_doctype == "Sales Order" else set_purchase_order_defaults
		return new_child_function(parent_doctype, parent_doctype_name, child_docname, item_row)

	def validate_quantity(child_item, d):
		if parent_doctype == "Sales Order" and flt(d.get("qty")) < flt(child_item.delivered_qty):
			frappe.throw(_("Cannot set quantity less than delivered quantity"))

		if parent_doctype == "Purchase Order" and flt(d.get("qty")) < flt(child_item.received_qty):
			frappe.throw(_("Cannot set quantity less than received quantity"))

	data = json.loads(trans_items)

	sales_doctypes = ['Sales Order', 'Sales Invoice', 'Delivery Note', 'Quotation']
	parent = frappe.get_doc(parent_doctype, parent_doctype_name)
	
	check_doc_permissions(parent, 'cancel')
	validate_and_delete_children(parent, data)

	for d in data:
		new_child_flag = False
		if not d.get("docname"):
			new_child_flag = True
			check_doc_permissions(parent, 'create')
			child_item = get_new_child_item(d)
		else:
			check_doc_permissions(parent, 'write')
			child_item = frappe.get_doc(parent_doctype + ' Item', d.get("docname"))

			prev_rate, new_rate = flt(child_item.get("rate")), flt(d.get("rate"))
			prev_qty, new_qty = flt(child_item.get("qty")), flt(d.get("qty"))
			prev_con_fac, new_con_fac = flt(child_item.get("conversion_factor")), flt(d.get("conversion_factor"))

			if parent_doctype == 'Sales Order':
				prev_date, new_date = child_item.get("delivery_date"), d.get("delivery_date")
			elif parent_doctype == 'Purchase Order':
				prev_date, new_date = child_item.get("schedule_date"), d.get("schedule_date")

			rate_unchanged = prev_rate == new_rate
			qty_unchanged = prev_qty == new_qty
			conversion_factor_unchanged = prev_con_fac == new_con_fac
			date_unchanged = prev_date == new_date if prev_date and new_date else False # in case of delivery note etc
			if rate_unchanged and qty_unchanged and conversion_factor_unchanged and date_unchanged:
				continue

		validate_quantity(child_item, d)

		child_item.qty = flt(d.get("qty"))
		precision = child_item.precision("rate") or 2

		if flt(child_item.billed_amt, precision) > flt(flt(d.get("rate")) * flt(d.get("qty")), precision):
			frappe.throw(_("Row #{0}: Cannot set Rate if amount is greater than billed amount for Item {1}.")
						 .format(child_item.idx, child_item.item_code))
		else:
			child_item.rate = flt(d.get("rate"))

		if d.get("conversion_factor"):
			if child_item.stock_uom == child_item.uom:
				child_item.conversion_factor = 1
			else:
				child_item.conversion_factor = flt(d.get('conversion_factor'))

		if d.get("delivery_date") and parent_doctype == 'Sales Order':
			child_item.delivery_date = d.get('delivery_date')

		if d.get("schedule_date") and parent_doctype == 'Purchase Order':
			child_item.schedule_date = d.get('schedule_date')

		if flt(child_item.price_list_rate):
			if flt(child_item.rate) > flt(child_item.price_list_rate):
				#  if rate is greater than price_list_rate, set margin
				#  or set discount
				child_item.discount_percentage = 0

				if parent_doctype in sales_doctypes:
					child_item.margin_type = "Amount"
					child_item.margin_rate_or_amount = flt(child_item.rate - child_item.price_list_rate,
						child_item.precision("margin_rate_or_amount"))
					child_item.rate_with_margin = child_item.rate
			else:
				child_item.discount_percentage = flt((1 - flt(child_item.rate) / flt(child_item.price_list_rate)) * 100.0,
					child_item.precision("discount_percentage"))
				child_item.discount_amount = flt(
					child_item.price_list_rate) - flt(child_item.rate)

				if parent_doctype in sales_doctypes:
					child_item.margin_type = ""
					child_item.margin_rate_or_amount = 0
					child_item.rate_with_margin = 0

		child_item.flags.ignore_validate_update_after_submit = True
		if new_child_flag:
			parent.load_from_db()
			child_item.idx = len(parent.items) + 1
			child_item.insert()
		else:
			child_item.save()

	parent.reload()
	parent.flags.ignore_validate_update_after_submit = True
	parent.set_qty_as_per_stock_uom()
	parent.calculate_taxes_and_totals()
	if parent_doctype == "Sales Order":
		make_packing_list(parent)
		parent.set_gross_profit()
	frappe.get_doc('Authorization Control').validate_approving_authority(parent.doctype,
		parent.company, parent.base_grand_total)

	parent.set_payment_schedule()
	if parent_doctype == 'Purchase Order':
		parent.validate_minimum_order_qty()
		parent.validate_budget()
	else:
		parent.check_credit_limit()
	parent.save()

	if parent_doctype == 'Purchase Order':
		update_last_purchase_rate(parent, is_submit = 1)
		parent.update_previous_doc_status()
		parent.update_requested_qty()
		parent.update_ordered_qty()
		parent.update_ordered_and_reserved_qty()
		parent.set_receipt_status()
		if parent.get("is_subcontracted"):
			parent.update_reserved_qty_for_subcontract()
	else:
		parent.update_reserved_qty()
		parent.update_previous_doc_status()
		parent.set_delivery_status(update=True)

	parent.reload()
	validate_workflow_conditions(parent)

	parent.update_blanket_order()
	parent.set_status()


@erpnext.allow_regional
def validate_regional(doc):
	pass
