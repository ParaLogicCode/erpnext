# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate, add_days
from frappe.model.document import Document
from erpnext.accounts.utils import get_balance_on
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_all_valid_dimension_fields


class BankReconciliation(Document):
	def validate(self):
		if not self.bank_account:
			frappe.throw(_("Please select Bank Account"))
		if not self.from_date or not self.to_date:
			frappe.throw(_("From Date and To Date are mandatory"))

		bank_account = frappe.get_doc("Bank Account", self.bank_account)
		if not bank_account.is_company_account:
			frappe.throw(_("Bank Account {0} is not a company account").format(self.bank_account))

		self.account = bank_account.account
		self.suspense_account = bank_account.suspense_account
		if not self.account:
			frappe.throw(_("{0} does not have a GL Account configured").format(
				frappe.get_desk_link("Bank Account", self.bank_account)
			))

		self._validate_mandatory()

	@frappe.whitelist()
	def set_payment_entries(self):
		self.validate()
		self.opening_balance = get_opening_balance(self.account, self.from_date)

		entries = self.get_payment_entries()
		self.set('payment_entries', [])
		for d in entries:
			d.amount = flt(d.get('debit', 0)) - flt(d.get('credit', 0))
			self.append('payment_entries', d)

	def get_payment_entries(self):
		account = self.suspense_account or self.account

		condition = ""
		if not self.allow_corrections:
			condition = "and ({0}clearance_date is null or {0}clearance_date='0000-00-00')"

		journal_entries = frappe.db.sql("""
			select 
				'Journal Entry' as voucher_type, je.name as voucher_no,
				'Journal Entry Account' as voucher_detail_dt, jea.name as voucher_detail_dn,
				jea.cheque_no as cheque_number, jea.cheque_date,
				jea.debit_in_account_currency as debit, jea.credit_in_account_currency as credit,
				je.posting_date, jea.against_account, jea.clearance_date, jea.account_currency,
				jea.party_type, jea.party, jea.party_name
			from `tabJournal Entry Account` jea
			inner join `tabJournal Entry` je on jea.parent = je.name
			where
				je.docstatus = 1
				and je.voucher_type != 'Bank Clearance Entry'
				and ifnull(je.is_opening, 'No') = 'No'
				and je.posting_date >= %(from)s and je.posting_date <= %(to)s
				and jea.account = %(account)s
				{0}
			order by je.posting_date, je.creation
		""".format(condition.format("jea.")), {
			"account": account, "from": self.from_date, "to": self.to_date
		}, as_dict=1)

		payment_entries = frappe.db.sql("""
			select
				'Payment Entry' as voucher_type, name as voucher_no,
				reference_no as cheque_number, reference_date as cheque_date,
				if(paid_from=%(account)s, 0, received_amount) as debit,
				if(paid_from=%(account)s, paid_amount, 0) as credit,
				posting_date,
				if(paid_from=%(account)s, paid_to, paid_from) as against_account,
				if(paid_to=%(account)s, paid_to_account_currency, paid_from_account_currency) as account_currency,
				clearance_date,
				party_type, party, party_name
			from `tabPayment Entry`
			where
				docstatus = 1
				and posting_date >= %(from)s and posting_date <= %(to)s
				and (paid_from=%(account)s or paid_to=%(account)s)
				{0}
			order by posting_date, creation
		""".format(condition.format("")), {
			"account": account, "from": self.from_date, "to": self.to_date
		}, as_dict=1)

		pos_sales_invoices = frappe.db.sql("""
			select
				'Sales Invoice' as voucher_type, si.name as voucher_no,
				'Sales Invoice Payment' as voucher_detail_dt, sip.name as voucher_detail_dn,
				sip.reference_no as cheque_number, sip.reference_date as cheque_date,
				sip.amount as debit, 0 as credit,
				si.posting_date,
				si.customer as against_account,
				'Customer' as party_type, si.customer as party, si.customer_name as party_name,
				sip.clearance_date,
				account.account_currency
			from `tabSales Invoice Payment` sip
			inner join `tabSales Invoice` si on sip.parent = si.name
			inner join `tabAccount` account on account.name = sip.account
			where
				si.docstatus=1
				and sip.account = %(account)s
				and si.posting_date >= %(from)s and si.posting_date <= %(to)s
				{0}
			order by si.posting_date, si.creation
		""".format(condition.format("sip.")), {
			"account": account, "from": self.from_date, "to": self.to_date
		}, as_dict=1)

		pos_purchase_invoices = frappe.db.sql("""
			select
				'Purchase Invoice' as voucher_type, pi.name as voucher_no,
				pi.paid_amount as credit, 0 as debit,
				pi.posting_date,
				pi.supplier as against_account,
				'Supplier' as party_type, pi.supplier as party, pi.supplier_name as party_name,
				pi.clearance_date,
				account.account_currency
			from `tabPurchase Invoice` pi
			inner join `tabAccount` account on account.name = pi.cash_bank_account
			where
				pi.docstatus = 1
				and pi.cash_bank_account = %(account)s
				and pi.posting_date >= %(from)s and pi.posting_date <= %(to)s
				{0}
			order by pi.posting_date, pi.creation
		""".format(condition.format("pi.")), {
			"account": account, "from": self.from_date, "to": self.to_date
		}, as_dict=1)

		entries = list(payment_entries) + list(journal_entries) + list(pos_sales_invoices) + list(pos_purchase_invoices)
		entries = sorted(entries, key=lambda k: getdate(k.posting_date) or getdate())

		return entries

	@frappe.whitelist()
	def update_clearance(self):
		self.validate()

		if not self.payment_entries:
			frappe.throw(_("No Payment Entries to update"))

		clearance_updated = False
		for d in self.get('payment_entries'):
			self.validate_payment_row(d)
			if d.clearance_date or self.allow_corrections:
				if self.update_row_clearance_date(d):
					clearance_updated = True

		if clearance_updated:
			if self.suspense_account:
				self.create_clearance_journal_entries()

			frappe.msgprint(_("Clearance Dates Updated"))
		else:
			frappe.msgprint(_("Clearance Dates not updated"))

		self.set_payment_entries()

	def validate_payment_row(self, row):
		if not row.voucher_type or not row.voucher_no:
			frappe.throw(_("Row #{0}: Voucher No is missing").format(row.idx))

		docstatus = frappe.db.get_value(row.voucher_type, row.voucher_no, "docstatus", for_update=True)
		if docstatus != 1:
			frappe.throw(_("Row #{0}: {1} is not submitted").format(
				row.idx, frappe.get_desk_link(row.voucher_type, row.voucher_no)
			))

		row.clearance_date = getdate(row.clearance_date) if row.clearance_date else None

		if row.clearance_date and row.clearance_date > getdate():
			frappe.throw(_("Row #{0}: Clearance Date {1} cannot in the future").format(
				row.idx, frappe.bold(row.get_formatted("clearance_date"))
			))

		if row.voucher_detail_dn:
			row.previous_clearance_date = frappe.db.get_value(row.voucher_detail_dt, row.voucher_detail_dn,
				'clearance_date', row.clearance_date, for_update=True)
		else:
			row.previous_clearance_date = frappe.db.get_value(row.voucher_type, row.voucher_no,
				'clearance_date', row.clearance_date, for_update=True)
		row.previous_clearance_date = getdate(row.previous_clearance_date) if row.previous_clearance_date else None

		if (
			not self.allow_corrections
			and row.previous_clearance_date
			and row.clearance_date
			and row.previous_clearance_date != row.clearance_date
		):
			frappe.throw(_("Row #{0}: {1} is already cleared").format(
				row.idx, frappe.get_desk_link(row.voucher_type, row.voucher_no)
			))

		if row.clearance_date and row.cheque_date and row.clearance_date < getdate(row.cheque_date):
			frappe.throw(_("Row #{0}: Clearance Date {1} cannot be before Reference/Cheque Date {2}").format(
				row.idx, frappe.bold(row.get_formatted("clearance_date")), frappe.bold(row.get_formatted("cheque_date"))
			))

	def update_row_clearance_date(self, row):
		if row.clearance_date == row.previous_clearance_date:
			return False

		if row.voucher_detail_dn:
			frappe.db.set_value(row.voucher_detail_dt, row.voucher_detail_dn, 'clearance_date', row.clearance_date,
				notify=True)
		else:
			frappe.db.set_value(row.voucher_type, row.voucher_no, 'clearance_date', row.clearance_date,
				notify=True)

		frappe.get_doc(dict(
			doctype='Version',
			ref_doctype=row.voucher_type,
			docname=row.voucher_no,
			data=frappe.as_json(dict(comment_type="Label", comment=_("Set Clearance Date to {0}".format(
				frappe.utils.formatdate(row.clearance_date) if row.clearance_date else "None"))))
		)).insert(ignore_permissions=True)

		return True

	def create_clearance_journal_entries(self):
		if not self.company:
			frappe.throw(_("Company is mandatory"))

		to_clear_map = {}
		to_reverse_map = {}
		for d in self.get('payment_entries'):
			if d.clearance_date != d.previous_clearance_date:
				if d.clearance_date:
					to_clear_map.setdefault(d.clearance_date, []).append(d)
				if d.previous_clearance_date:
					to_reverse_map.setdefault(d.previous_clearance_date, []).append(d)

		reversal_jvs = []
		for clearance_date, rows in to_reverse_map.items():
			je = self.make_clearance_journal_entry(clearance_date, rows, is_reversal=True)
			je.save()
			je.submit()
			reversal_jvs.append(je.name)

		clearance_jvs = []
		for clearance_date, rows in to_clear_map.items():
			je = self.make_clearance_journal_entry(clearance_date, rows, is_reversal=False)
			je.save()
			je.submit()
			clearance_jvs.append(je.name)

		if reversal_jvs:
			frappe.msgprint(_("Clearing Reversal Journal Entries created:<br>{0}").format(
				", ".join([frappe.utils.get_link_to_form("Journal Entry", name) for name in reversal_jvs])
			))

		if clearance_jvs:
			frappe.msgprint(_("Bank Clearing Journal Entries created:<br>{0}").format(
				", ".join([frappe.utils.get_link_to_form("Journal Entry", name) for name in clearance_jvs])
			))

	def make_clearance_journal_entry(self, clearance_date, rows, is_reversal=False):
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Bank Clearance Entry"
		je.company = self.company
		je.branch = self.branch
		je.posting_date = clearance_date

		if is_reversal:
			je.user_remark = _("Bank reconciliation correction reversal entry")
		else:
			je.user_remark = _("Bank reconciliation clearance entry")

		total_amount = 0
		for d in rows:
			amount = d.amount
			if is_reversal:
				amount = -amount

			je_row = je.append("accounts", {
				"account": self.account,
				"debit_in_account_currency": abs(amount) if amount > 0 else 0,
				"credit_in_account_currency": abs(amount) if amount < 0 else 0,
				"cheque_no": d.cheque_number,
				"cheque_date": d.cheque_date,
				"clearance_date": clearance_date,
			})
			je_row.update(self.get_row_additional_values(d))

			total_amount += amount

		je.append("accounts", {
			"account": self.suspense_account,
			"debit_in_account_currency": abs(total_amount) if total_amount < 0 else 0,
			"credit_in_account_currency": abs(total_amount) if total_amount > 0 else 0,
			"clearance_date": clearance_date,
		})

		return je

	def get_row_additional_values(self, row):
		dimensions = self.get_parent_document_dimensions(row.voucher_type, row.voucher_no)
		if row.voucher_detail_dn:
			dimensions.update(get_document_dimensions(row.voucher_detail_dt, row.voucher_detail_dn))

		return dimensions

	def get_parent_document_dimensions(self, voucher_type, voucher_no):
		if not self.get("_parent_document_dimensions"):
			self._parent_document_dimensions = {}

		key = (voucher_type, voucher_no)
		if key not in self._parent_document_dimensions:
			dimensions = get_document_dimensions(voucher_type, voucher_no)
			self._parent_document_dimensions[key] = dimensions

		return self._parent_document_dimensions[key]


@frappe.whitelist()
def get_opening_balance(account, from_date):
	from_date = getdate(from_date)
	from_date = add_days(from_date, -1)

	return get_balance_on(account, from_date)


def get_document_dimensions(doctype, name):
	dimension_fields = get_all_valid_dimension_fields(doctype)
	if frappe.get_meta(doctype).has_field("user_remark"):
		dimension_fields.append("user_remark")

	dimensions = {}
	if dimension_fields:
		dimensions = frappe.db.get_value(doctype, name, dimension_fields, as_dict=True) or {}

	dimensions = {f: v for f, v in dimensions.items() if v}
	return dimensions
