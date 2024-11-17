# Copyright (c) 2015, Frappe Technologies Pvt. Ltd.
# License: GNU General Public License v3. See license.txt

import frappe
import erpnext
from frappe import _, scrub
from frappe.utils import getdate, flt, cint, formatdate, cstr
from frappe.desk.query_report import group_report_data
from erpnext.accounts.report.financial_statements import get_cost_centers_with_children
from erpnext.accounts.utils import get_currency_precision


class ReceivablePayableReport(object):
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or {})
		self.currency_precision = get_currency_precision() or 2
		self.has_cost_center = False
		self.has_project = False

		self.advance_against_voucher_types = get_advance_against_voucher_types()

	def run(self, args):
		self.validate_filters(args)

		data = self.get_data()
		columns = self.get_columns()

		grouped_data = self.get_grouped_data(columns, data)
		chart = self.get_chart_data(data)

		return columns, grouped_data, None, chart

	def validate_filters(self, args):
		self.filters.party_type = args.get('party_type')
		self.party_naming_by = frappe.db.get_single_value(args.get("naming_by")[0], args.get("naming_by")[1])

		self.filters.report_date = getdate(self.filters.report_date)
		self.age_as_on = getdate() if self.filters.report_date > getdate() else self.filters.report_date

		self.validate_ageing_filter()

		if not self.filters.get("company"):
			self.filters.company = erpnext.get_default_company()
		self.company_currency = frappe.get_cached_value('Company', self.filters.company, "default_currency")

		if self.filters.get('cost_center'):
			self.filters.cost_center = get_cost_centers_with_children(self.filters.get("cost_center"))

		if self.filters.get("project"):
			if not isinstance(self.filters.get("project"), list):
				self.filters.project = [d.strip() for d in cstr(self.filters.project).strip().split(',') if d]

		if self.filters.get("sales_person"):
			sales_person = self.filters.sales_person
			self.filters.sales_person = frappe.get_all("Sales Person", filters={
				"name": ["descendants of", self.filters.sales_person]
			})
			self.filters.sales_person = set([sales_person] + [d.name for d in self.filters.sales_person])

		self.dr_or_cr = "debit" if erpnext.get_party_account_type(self.filters.party_type) == "Receivable" else "credit"
		if self.filters.get("party_type") == "Employee":
			self.dr_or_cr = "debit"

		self.reverse_dr_or_cr = "credit" if self.dr_or_cr == "debit" else "debit"

	def validate_ageing_filter(self):
		self.ageing_range = [cint(r.strip()) for r in self.filters.get('ageing_range', "").split(",") if r]
		self.ageing_range = sorted(list(set(self.ageing_range)))
		self.ageing_column_count = len(self.ageing_range) + 1

	def get_data(self):
		self.get_gl_entries()
		self.get_pdc_details()
		self.get_return_entries()
		self.get_sales_persons_map()
		self.get_projects_map()
		self.get_employee_advance_map()

		employee_advances_added = set()
		gles_to_add = []
		for gle in self.gl_entries_till_date:
			if (
				self.is_receivable_or_payable(gle)
				and self.is_in_cost_center(gle)
				and self.is_in_project(gle)
				and self.is_in_sales_person(gle)
				and self.is_in_item_filtered_invoice(gle)
			):
				gle.outstanding_amount, gle.return_amount, gle.payment_amount = self.get_outstanding_amount(
					gle, self.filters.report_date,
				)

				if abs(gle.outstanding_amount) >= 0.1 / 10 ** self.currency_precision:
					gles_to_add.append(gle)

			elif self.filters.party_type == "Employee" and gle.against_voucher_type == "Employee Advance":
				ea_details = self.employee_advance_map.get(gle.against_voucher, frappe._dict())
				if (
					gle.against_voucher not in employee_advances_added
					and self.is_in_cost_center(ea_details)
					and self.is_in_project(ea_details)
				):
					employee_advances_added.add(gle.against_voucher)
					gle.outstanding_amount, gle.return_amount, gle.payment_amount = self.get_employee_advance_outstanding(
						gle, self.filters.report_date
					)

					if abs(gle.outstanding_amount) >= 0.1 / 10 ** self.currency_precision:
						ea = gle.copy()
						ea.credit = 0
						ea.debit = gle.payment_amount
						ea.voucher_type = gle.against_voucher_type
						ea.voucher_no = gle.against_voucher
						ea.remarks = ea_details.purpose
						ea.cost_center = ea_details.cost_center
						ea.project = ea_details.project

						gles_to_add.append(ea)

		parties = {gle.party for gle in gles_to_add}
		self.get_party_map(parties)
		self.get_voucher_details_map(gles_to_add)
		self.get_delivery_notes_map(gles_to_add)

		data = []
		for gle in gles_to_add:
			row = self.prepare_row(gle)
			data.append(row)

		return data

	def get_gl_entries(self):
		conditions, values = self.prepare_conditions()

		if self.use_account_currency():
			select_fields = "sum(gle.debit_in_account_currency) as debit, sum(gle.credit_in_account_currency) as credit"
		else:
			select_fields = "sum(gle.debit) as debit, sum(gle.credit) as credit"

		self.gl_entries = frappe.db.sql(f"""
			select
				gle.name, gle.posting_date, gle.due_date,
				gle.account, gle.party_type, gle.party,
				gle.voucher_type, gle.voucher_no,
				gle.against_voucher_type, gle.against_voucher,
				gle.account_currency, gle.remarks,
				gle.cost_center, gle.project,
				{select_fields}
			from `tabGL Entry` gle
			where gle.party_type = %s
				and (gle.party is not null and gle.party != '')
				{conditions}
			group by gle.voucher_type, gle.voucher_no, gle.against_voucher_type, gle.against_voucher, gle.party
			order by gle.posting_date, gle.party
		""", values, as_dict=True)

		self.future_gl_entries = []
		self.gl_entries_till_date = []

		self.gl_entries_map = {}
		self.future_vouchers = set()
		self.vouchers_till_date = set()
		self.voucher_nos_till_date = set()

		for gle in self.gl_entries:
			if gle.posting_date > self.filters.report_date:
				self.future_gl_entries.append(gle)
				self.future_vouchers.add((gle.voucher_type, gle.voucher_no))
			else:
				self.gl_entries_till_date.append(gle)
				self.vouchers_till_date.add((gle.voucher_type, gle.voucher_no))
				self.voucher_nos_till_date.add(gle.voucher_no)

			if gle.against_voucher_type and gle.against_voucher:
				key = (gle.party, gle.against_voucher_type, gle.against_voucher)
				self.gl_entries_map.setdefault(key, []).append(gle)

		return self.gl_entries

	def prepare_conditions(self):
		conditions = [""]
		values = [self.filters.party_type]

		if self.filters.company:
			conditions.append("gle.company = %s")
			values.append(self.filters.company)

		if self.filters.finance_book:
			conditions.append("ifnull(gle.finance_book,'') in (%s, '')")
			values.append(self.filters.finance_book)

		party = self.get_filter_party()
		if party:
			conditions.append("gle.party = %s")
			values.append(party)

		if self.filters.party_type == "Customer":
			account_type = "Receivable"
			if self.filters.get("customer_group"):
				lft, rgt = frappe.db.get_value("Customer Group",
					self.filters.get("customer_group"), ["lft", "rgt"])

				conditions.append("""gle.party in (select name from tabCustomer
					where exists(select name from `tabCustomer Group` where lft >= {0} and rgt <= {1}
						and name=tabCustomer.customer_group))""".format(lft, rgt))

			if self.filters.get("territory"):
				lft, rgt = frappe.db.get_value("Territory",
					self.filters.get("territory"), ["lft", "rgt"])

				conditions.append("""gle.party in (select name from tabCustomer
					where exists(select name from `tabTerritory` where lft >= {0} and rgt <= {1}
						and name=tabCustomer.territory))""".format(lft, rgt))

			if self.filters.get("payment_terms_template"):
				conditions.append("gle.party in (select name from tabCustomer where payment_terms=%s)")
				values.append(self.filters.get("payment_terms_template"))

			if self.filters.get("sales_partner"):
				conditions.append("gle.party in (select name from tabCustomer where default_sales_partner=%s)")
				values.append(self.filters.get("sales_partner"))

		elif self.filters.party_type == "Supplier":
			account_type = "Payable"
			if self.filters.get("supplier_group"):
				conditions.append("""gle.party in (select name from tabSupplier
					where supplier_group=%s)""")
				values.append(self.filters.get("supplier_group"))

		elif self.filters.party_type == "Employee":
			account_type = ['in', ['Payable', 'Receivable']]
			if self.filters.get("department"):
				lft, rgt = frappe.db.get_value("Department",
					self.filters.get("department"), ["lft", "rgt"])

				conditions.append("""gle.party in (select name from tabEmployee
					where exists(select name from `tabDepartment` where lft >= {0} and rgt <= {1}
						and name=tabEmployee.department))""".format(lft, rgt))

			if self.filters.get("designation"):
				conditions.append("gle.party in (select name from tabEmployee where designation=%s)")
				values.append(self.filters.get("designation"))

			if self.filters.get("branch"):
				conditions.append("gle.party in (select name from tabEmployee where branch=%s)")
				values.append(self.filters.get("branch"))

		if self.filters.get("account"):
			accounts = [self.filters.get("account")]
		else:
			accounts = [d.name for d in frappe.get_all("Account",
			filters={"account_type": account_type, "company": self.filters.company})]
		conditions.append("gle.account in (%s)" % ','.join(['%s'] *len(accounts)))
		values += accounts

		return " and ".join(conditions), values

	def get_gl_entries_for(self, party, against_voucher_type, against_voucher):
		key = (party, against_voucher_type, against_voucher)
		return self.gl_entries_map.get(key, [])

	def get_pdc_details(self):
		self.pdc_details = frappe._dict()

		pdc_via_pe = frappe.db.sql("""
			select
				pref.reference_name as invoice_no, pent.party, pent.party_type,
				pent.posting_date as pdc_date,
				ifnull(pref.allocated_amount, 0) as pdc_amount,
				pent.reference_no as pdc_ref
			from `tabPayment Entry` as pent
			inner join `tabPayment Entry Reference` as pref on pref.parent = pent.name
			where pent.docstatus < 2 and pent.posting_date > %s and pent.party_type = %s
		""", (self.filters.report_date, self.filters.party_type), as_dict=1)

		for pdc in pdc_via_pe:
			self.pdc_details.setdefault((pdc.invoice_no, pdc.party), []).append(pdc)

		if self.use_account_currency():
			amount_field = ("jea.debit_in_account_currency"
				if erpnext.get_party_account_type(self.filters.party_type) == 'Payable' else "jea.credit_in_account_currency")
		else:
			amount_field = "jea.debit" if erpnext.get_party_account_type(self.filters.party_type) == 'Payable' else "jea.credit"

		pdc_via_je = frappe.db.sql("""
			select
				jea.reference_name as invoice_no, jea.party, jea.party_type,
				je.posting_date as pdc_date, ifnull({0}, 0) as pdc_amount,
				jea.cheque_no as pdc_ref
			from `tabJournal Entry` as je
			inner join `tabJournal Entry Account` as jea on jea.parent = je.name
			where je.docstatus < 2 and je.posting_date > %s and jea.party_type = %s
		""".format(amount_field), (self.filters.report_date, self.filters.party_type), as_dict=1)

		for pdc in pdc_via_je:
			self.pdc_details.setdefault((pdc.invoice_no, pdc.party), []).append(pdc)

		return self.pdc_details

	def get_return_entries(self):
		self.return_entries = {}

		doctype = self.get_invoice_doctype()
		if not doctype:
			return self.return_entries

		self.return_entries = dict(frappe.get_all(
			doctype,
			filters={"is_return": 1, "docstatus": 1},
			fields=["name", "return_against"],
			order_by=None,
			as_list=1,
		))
		return self.return_entries

	def get_sales_persons_map(self):
		self.sales_persons_map = {}

		if self.filters.party_type == "Customer" and self.voucher_nos_till_date:
			data = frappe.db.sql("""
				select parenttype, parent, sales_person
				from `tabSales Team`
				where parent in %s and docstatus = 1 
			""", [self.voucher_nos_till_date], as_dict=1)

			for d in data:
				self.sales_persons_map.setdefault((d.parenttype, d.parent), []).append(d.sales_person)

		return self.sales_persons_map

	def get_projects_map(self):
		self.projects_map = {}

		invoice_doctype = self.get_invoice_doctype()
		if invoice_doctype:
			invoices = [voucher_no for voucher_type, voucher_no in self.vouchers_till_date if voucher_type == invoice_doctype]
			if invoices:
				data = frappe.db.sql(f"""
					select name, project
					from `tab{invoice_doctype}`
					where docstatus = 1 and project is not null and project != '' and name in %s
				""", [invoices], as_dict=1)

				for d in data:
					self.projects_map[(invoice_doctype, d.name)] = d.project

		return self.projects_map

	def get_employee_advance_map(self):
		self.employee_advance_map = {}
		if self.filters.party_type != "Employee":
			return self.employee_advance_map

		names = {d.against_voucher for d in self.gl_entries_till_date if d.against_voucher_type == "Employee Advance"}
		if not names:
			return self.employee_advance_map

		employee_advances = frappe.db.sql("""
			select ea.name, ea.cost_center, ea.project, ea.purpose
			from `tabEmployee Advance` ea
			where ea.docstatus = 1 and ea.name in %s
			""", [names], as_dict=1)

		for d in employee_advances:
			self.employee_advance_map[d.name] = d

		return self.employee_advance_map

	def get_voucher_details_map(self, gles_to_add):
		self.voucher_details = {}

		if self.filters.party_type == "Customer":
			invoices = {gle.voucher_no for gle in gles_to_add if gle.voucher_type == "Sales Invoice"}
			if invoices:
				data = frappe.db.sql("""
					select name, due_date, po_no, contact_person, territory
					from `tabSales Invoice`
					where docstatus = 1 and name in %s
				""", [invoices], as_dict=1)
				for d in data:
					self.voucher_details[("Sales Invoice", d.name)] = d

		if self.filters.party_type == "Supplier":
			invoices = {gle.voucher_no for gle in gles_to_add if gle.voucher_type == "Purchase Invoice"}
			if invoices:
				data = frappe.db.sql("""
					select name, due_date, bill_no, bill_date, contact_person
					from `tabPurchase Invoice`
					where docstatus = 1 and name in %s
				""", [invoices], as_dict=1)
				for d in data:
					self.voucher_details[("Purchase Invoice", d.name)] = d

		journal_entries = {gle.voucher_no for gle in gles_to_add if gle.voucher_type == "Journal Entry"}
		if journal_entries:
			data = frappe.db.sql("""
				select name, due_date, bill_no, bill_date
				from `tabJournal Entry`
				where docstatus = 1 and name in %s
				""", [journal_entries], as_dict=1)

			for d in data:
				self.voucher_details[("Journal Entry", d.name)] = d

		return self.voucher_details

	def get_delivery_notes_map(self, gles_to_add):
		self.delivery_notes_map = {}
		if self.filters.party_type != "Customer":
			return self.delivery_notes_map

		invoices = {gle.voucher_no for gle in gles_to_add if gle.voucher_type == "Sales Invoice"}
		if not invoices:
			return self.delivery_notes_map

		invoice_data = frappe.db.sql("""
			select parent as sales_invoice, delivery_note
			from `tabSales Invoice Item`
			where docstatus = 1 and delivery_note is not null and delivery_note != '' and parent in %s
		""", [invoices], as_dict=1)

		for d in invoice_data:
			self.delivery_notes_map.setdefault(d.sales_invoice, set()).add(d.delivery_note)

		delivery_data = frappe.db.sql("""
			select sales_invoice, parent as delivery_note
			from `tabDelivery Note Item`
			where docstatus = 1 and sales_invoice in %s
		""", [invoices], as_dict=1)

		for d in delivery_data:
			self.delivery_notes_map.setdefault(d.sales_invoice, set()).add(d.delivery_note)

		return self.delivery_notes_map

	def get_party_map(self, parties):
		self.party_map = {}

		if parties:
			if self.filters.party_type == "Customer":
				party_data = frappe.db.sql("""
					select
						p.name, p.customer_name,
						p.territory, p.customer_group,
						p.customer_primary_contact as contact_person,
						p.payment_terms, p.tax_id,
						GROUP_CONCAT(steam.sales_person SEPARATOR ', ') as sales_person
					from `tabCustomer` p
					left join `tabSales Team` steam on steam.parent = p.name and steam.parenttype = 'Customer'
					where p.name in %s
					group by p.name
				""", [parties], as_dict=True)

			elif self.filters.party_type == "Supplier":
				party_data = frappe.db.sql("""
					select p.name, p.supplier_name, p.supplier_group, p.tax_id, p.payment_terms
					from `tabSupplier` p
					where p.name in %s
				""", [parties], as_dict=True)

			elif self.filters.party_type == "Employee":
				party_data = frappe.db.sql("""
					select p.name, p.employee_name, p.department, p.designation, p.employment_type
					from `tabEmployee` p
					where p.name in %s
				""", [parties], as_dict=True)
			else:
				party_data = []

			self.party_map = {d.name: d for d in party_data}

		return self.party_map

	def is_receivable_or_payable(self, gle):
		return (
			# advance
			(not gle.against_voucher)

			# against sales order/purchase order
			or (gle.against_voucher_type in self.advance_against_voucher_types)

			# sales invoice/purchase invoice
			or (
				gle.against_voucher == gle.voucher_no
				and gle.get(self.dr_or_cr) - gle.get(self.reverse_dr_or_cr) > 0
			)

			# standalone credit notes
			or (
				gle.against_voucher == gle.voucher_no
				and gle.voucher_no in self.return_entries
				and not self.return_entries.get(gle.voucher_no)
			)

			# entries adjusted with future vouchers
			or ((gle.against_voucher_type, gle.against_voucher) in self.future_vouchers)
		)

	def is_in_cost_center(self, gle):
		if self.filters.get("cost_center"):
			return gle.cost_center and gle.cost_center in self.filters.cost_center
		else:
			return True

	def is_in_project(self, gle):
		project = gle.project
		if not project:
			project = self.projects_map.get((gle.voucher_type, gle.voucher_no))

		if self.filters.get("project"):
			return project and project in self.filters.project
		else:
			return True

	def is_in_sales_person(self, gle):
		if self.filters.get("sales_person"):
			sales_persons = (
				self.get_sales_persons(gle.voucher_type, gle.voucher_no)
				or self.get_sales_persons(gle.against_voucher_type, gle.against_voucher)
			)
			return bool([sp for sp in sales_persons if sp in self.filters.sales_person])
		else:
			return True

	def is_in_item_filtered_invoice(self, gle):
		if self.filters.get("has_item"):
			return gle.voucher_type == self.get_invoice_doctype() and gle.voucher_no in self.get_item_filtered_invoices()
		else:
			return True

	def get_item_filtered_invoices(self):
		if not self.filters.get('has_item') or self.filters.get("party_type") not in ['Customer', 'Supplier']:
			return []

		if not hasattr(self, 'item_filtered_invoices'):
			item_doctype = self.get_invoice_doctype()
			self.item_filtered_invoices = set(frappe.db.sql_list("""
				select distinct parent from `tab{dt} Item` where item_code = %s
			""".format(dt=item_doctype), self.filters.get('has_item')))

		return self.item_filtered_invoices

	def get_outstanding_amount(self, gle, report_date):
		payment_amount, credit_note_amount = 0.0, 0.0

		for e in self.get_gl_entries_for(gle.party, gle.voucher_type, gle.voucher_no):
			if getdate(e.posting_date) <= report_date and e.name != gle.name:
				amount = flt(e.get(self.reverse_dr_or_cr), self.currency_precision) - flt(e.get(self.dr_or_cr), self.currency_precision)
				if e.voucher_no not in self.return_entries:
					payment_amount += amount
				else:
					credit_note_amount += amount

		# for stand alone credit/debit note
		if gle.voucher_no in self.return_entries and flt(gle.get(self.reverse_dr_or_cr)) - flt(gle.get(self.dr_or_cr) > 0):
			amount = flt(gle.get(self.reverse_dr_or_cr), self.currency_precision) - flt(gle.get(self.dr_or_cr), self.currency_precision)
			credit_note_amount += amount
			payment_amount -= amount

		outstanding_amount = (flt((flt(gle.get(self.dr_or_cr), self.currency_precision)
			- flt(gle.get(self.reverse_dr_or_cr), self.currency_precision)
			- payment_amount - credit_note_amount), self.currency_precision))

		credit_note_amount = flt(credit_note_amount, self.currency_precision)

		return outstanding_amount, credit_note_amount, payment_amount

	def get_employee_advance_outstanding(self, gle, report_date):
		claimed_amount, payment_amount, return_amount = 0.0, 0.0, 0.0

		for e in self.get_gl_entries_for(gle.party, gle.against_voucher_type, gle.against_voucher):
			if getdate(e.posting_date) <= report_date:
				payment_amount += flt(e.debit, self.currency_precision)

				if e.voucher_type == "Expense Claim":
					claimed_amount += flt(e.credit, self.currency_precision)
				else:
					return_amount += flt(e.credit, self.currency_precision)

		outstanding_amount = payment_amount - claimed_amount - return_amount
		return outstanding_amount, return_amount, payment_amount

	def prepare_row(self, gle):
		row = frappe._dict()

		voucher_details = self.voucher_details.get((gle.voucher_type, gle.voucher_no), frappe._dict())

		# Voucher and dates
		row["voucher_type"] = gle.voucher_type
		row["voucher_no"] = gle.voucher_no
		row["remarks"] = gle.remarks

		row["posting_date"] = gle.posting_date
		row["due_date"] = voucher_details.get("due_date") or gle.due_date

		row["bill_no"] = voucher_details.get("bill_no")
		row["bill_date"] = voucher_details.get("bill_date")

		# Party, accounts and dimensions
		row["party"] = gle.party
		row["party_name"] = self.get_party_name(gle.party)

		row["account"] = gle.account
		row["cost_center"] = gle.cost_center
		row["project"] = gle.project or self.projects_map.get((gle.voucher_type, gle.voucher_no))

		if row.cost_center:
			self.has_cost_center = True
		if row.project:
			self.has_project = True

		# Voucher Details
		row["po_no"] = voucher_details.get("po_no")
		if gle.voucher_type == "Sales Invoice":
			row["delivery_note"] = ", ".join(self.delivery_notes_map.get(gle.voucher_no, []))

		# Party Details
		if self.filters.get("party_type") == "Customer":
			row["customer_group"] = self.get_customer_group(gle.party)
			row["territory"] = self.get_territory(gle.party, gle.voucher_type, gle.voucher_no)
			row["contact"] = self.get_contact_person(gle.party, gle.voucher_type, gle.voucher_no)

			row["sales_person"] = ", ".join(
				self.get_sales_persons(gle.voucher_type, gle.voucher_no)
				or self.get_sales_persons(gle.against_voucher_type, gle.against_voucher)
			)

		if self.filters.get("party_type") == "Supplier":
			row["supplier_group"] = self.get_supplier_group(gle.party)

		# Amounts
		invoiced_amount = gle.get(self.dr_or_cr) if gle.get(self.dr_or_cr) > 0 else 0
		paid_amt = invoiced_amount - gle.outstanding_amount - gle.return_amount

		row["invoiced_amount"] = invoiced_amount
		row["paid_amount"] = paid_amt
		row["return_amount"] = gle.return_amount
		row["outstanding_amount"] = gle.outstanding_amount

		row["currency"] = gle.account_currency if self.use_account_currency() else self.company_currency
		self.account_currency = row["currency"]

		# ageing data
		if self.filters.ageing_based_on == "Due Date":
			entry_date = row.due_date or gle.posting_date
		elif self.filters.ageing_based_on == "Supplier Invoice Date":
			entry_date = row.bill_date or gle.posting_date
		else:
			entry_date = gle.posting_date

		row["age"], ageing_data = get_ageing_data(self.ageing_range, self.age_as_on, entry_date, gle.outstanding_amount)
		for i, age_range_value in enumerate(ageing_data):
			row["range{0}".format(i+1)] = age_range_value

		# issue 6371-Ageing buckets should not have amounts if due date is not reached
		if (
			self.filters.ageing_based_on == "Due Date"
			and getdate(row.due_date or gle.posting_date) > getdate(self.filters.report_date)
		):
			for i in range(self.ageing_column_count):
				row["range{}".format(i+1)] = 0

		if (
			self.filters.ageing_based_on == "Supplier Invoice Date"
			and getdate(row.bill_date or gle.posting_date) > getdate(self.filters.report_date)
		):
			for i in range(self.ageing_column_count):
				row["range{}".format(i+1)] = 0

		# PDC
		pdc_list = self.pdc_details.get((gle.voucher_no, gle.party), [])
		pdc_amount = 0
		pdc_details = []
		for d in pdc_list:
			pdc_amount += flt(d.pdc_amount)
			if pdc_amount and d.pdc_ref and d.pdc_date:
				pdc_details.append(cstr(d.pdc_ref) + "/" + formatdate(d.pdc_date))

		remaining_balance = gle.outstanding_amount - flt(pdc_amount)
		pdc_details = ", ".join(pdc_details)

		row["pdc/lc_ref"] = pdc_details
		row["pdc/lc_amount"] = pdc_amount
		row["remaining_balance"] = remaining_balance

		return row

	def get_grouped_data(self, columns, data):
		level1 = self.filters.get("group_by", "").replace("Group by ", "")
		level2 = self.filters.get("group_by_2", "").replace("Group by ", "")
		level1_fieldname = "party" if level1 in ['Customer', 'Supplier', 'Employee'] else scrub(level1)
		level2_fieldname = "party" if level2 in ['Customer', 'Supplier', 'Employee'] else scrub(level2)

		group_by = [None]
		group_by_labels = {}
		if level1:
			group_by.append(level1_fieldname)
			group_by_labels[level1_fieldname] = level1
		if level2:
			group_by.append(level2_fieldname)
			group_by_labels[level2_fieldname] = level2

		if len(group_by) <= 1:
			return self.group_aggregate_age(data, columns)

		total_fields = [c['fieldname'] for c in columns
			if c['fieldtype'] in ['Float', 'Currency', 'Int'] and c['fieldname'] != 'age']

		def postprocess_group(group_object, grouped_by):
			if not group_object.group_field:
				group_object.totals['party'] = "'Total'"
			elif group_object.group_field == 'party':
				group_object.totals['party'] = group_object.group_value
				group_object.totals['party_name'] = group_object.rows[0].get('party_name')
			else:
				group_object.totals['party'] = "'{0}: {1}'".format(group_object.group_label, group_object.group_value)

			if group_object.group_field == 'party':
				group_object.totals['currency'] = group_object.rows[0].get("currency")
				group_object.tax_id = self.party_map.get(group_object.group_value, {}).get("tax_id")
				group_object.payment_terms = self.party_map.get(group_object.group_value, {}).get("payment_terms")
				group_object.credit_limit = self.party_map.get(group_object.group_value, {}).get("credit_limit")

			group_object.rows = self.group_aggregate_age(group_object.rows, columns, grouped_by)
			if group_object.rows is None:
				group_object.totals = None

		return group_report_data(data, group_by, total_fields=total_fields, postprocess_group=postprocess_group,
			group_by_labels=group_by_labels)

	def group_aggregate_age(self, data, columns, grouped_by=None):
		if not self.filters.from_date and not self.filters.to_date:
			return data

		within_limit = []
		below_limit = []
		above_limit = []

		if self.filters.ageing_based_on == "Due Date":
			date_field = "due_date"
		elif self.filters.ageing_based_on == "Supplier Invoice Date":
			date_field = "bill_date"
		else:
			date_field = "posting_date"

		for d in data:
			if d._isGroupTotal or d._isGroup:
				within_limit.append(d)
			elif self.filters.from_date and d[date_field] < getdate(self.filters.from_date):
				below_limit.append(d)
			elif self.filters.to_date and d[date_field] > getdate(self.filters.to_date):
				above_limit.append(d)
			else:
				within_limit.append(d)

		if not within_limit:
			return None

		if not below_limit and not above_limit:
			return data

		total_fields = [c['fieldname'] for c in columns
			if c['fieldtype'] in ['Float', 'Currency', 'Int'] and c['fieldname'] != 'age']

		below_limit_total = group_report_data(below_limit, None, total_fields=total_fields, totals_only=True)
		below_limit_total = below_limit_total[0] if below_limit_total else {}
		above_limit_total = group_report_data(above_limit, None, total_fields=total_fields, totals_only=True)
		above_limit_total = above_limit_total[0] if above_limit_total else {}
		within_limit_total = group_report_data(within_limit, None, total_fields=total_fields, totals_only=True)
		within_limit_total = within_limit_total[0] if within_limit_total else {}

		if grouped_by:
			below_limit_total.update(grouped_by)
			within_limit_total.update(grouped_by)
			above_limit_total.update(grouped_by)

		below_limit_total['party'] = _("'Before {0} Total'").format(formatdate(self.filters.from_date))
		above_limit_total['party'] = _("'After {0} Total'").format(formatdate(self.filters.to_date))

		within_limit_total['_excludeFromTotal'] = True
		within_limit_total['_bold'] = True
		if self.filters.from_date and self.filters.to_date:
			within_limit_total['party'] = _("'Total Between {0} and {1}'").format(formatdate(self.filters.from_date), formatdate(self.filters.to_date))
		elif self.filters.from_date:
			within_limit_total['party'] = _("'Total of {0} and Above'").format(formatdate(self.filters.from_date))
		elif self.filters.to_date:
			within_limit_total['party'] = _("'Total of {0} and Below'").format(formatdate(self.filters.to_date))

		out = []
		if self.filters.to_date:
			out.append(above_limit_total)

		if within_limit:
			if self.filters.to_date:
				out.append({})

			out += within_limit
			out.append(within_limit_total)

			if self.filters.from_date:
				out.append({})

		if self.filters.from_date:
			out.append(below_limit_total)

		return out

	def get_chart_data(self, data):
		rows = []
		for d in data:
			rows.append({'values': [d["range{}".format(i+1)] for i in range(self.ageing_column_count)]})

		return {
			"data": {
				"labels": [col.get('label') for col in self.ageing_columns],
				"datasets": rows
			},
			"colors": ['light-blue', 'blue', 'purple', 'orange', 'red'],
			"type": 'percentage',
			"fieldtype": "Currency",
			"options": getattr(self, "account_currency", None)
		}

	def get_invoice_doctype(self):
		if self.filters.get("party_type") in ['Customer', 'Supplier']:
			return "Sales Invoice" if self.filters.get("party_type") == "Customer" else "Purchase Invoice"

	def get_party_name(self, party):
		return self.party_map.get(party, {}).get(frappe.scrub(self.filters.party_type) + "_name", "")

	def get_contact_person(self, party, voucher_type=None, voucher_no=None):
		contact_person = None

		if voucher_type and voucher_no:
			contact_person = self.voucher_details.get((voucher_type, voucher_no), {}).get("contact_person")
		if not contact_person:
			contact_person = self.party_map.get(party, {}).get("contact_person")

		return contact_person

	def get_territory(self, party, voucher_type=None, voucher_no=None):
		territory = None

		if voucher_type and voucher_no:
			territory = self.voucher_details.get((voucher_type, voucher_no), {}).get("territory")
		if not territory:
			territory = self.party_map.get(party, {}).get("territory")

		return territory

	def get_customer_group(self, party):
		return self.party_map.get(party, {}).get("customer_group")

	def get_supplier_group(self, party):
		return self.party_map.get(party, {}).get("supplier_group")

	def get_sales_persons(self, voucher_type, voucher_no):
		if not voucher_type or not voucher_no:
			return []
		return self.sales_persons_map.get((voucher_type, voucher_no)) or []

	def use_account_currency(self):
		return self.get_filter_party() or self.filters.get("account")

	def get_filter_party(self):
		return self.filters.get(scrub(self.filters.get("party_type")))

	def get_columns(self):
		party_column_width = 80 if self.party_naming_by == "Naming Series" else 200

		columns = [
			{
				"label": _("Date"),
				"fieldtype": "Date",
				"fieldname": "posting_date",
				"width": 80
			},
			{
				"label": _(self.filters.get("party_type")),
				"fieldtype": "Link",
				"fieldname": "party",
				"filter_fieldname": scrub(self.filters.get("party_type")),
				"options": self.filters.get("party_type"),
				"width": party_column_width if not self.filters.get("group_by") else 300
			}
		]

		if self.filters.get("group_by"):
			columns = list(reversed(columns))

		if self.party_naming_by == "Naming Series":
			columns.append({
				"label": _(self.filters.get("party_type") + " Name"),
				"fieldtype": "Data",
				"fieldname": "party_name",
				"width": 180
			})

		columns += [
			{
				"label": _("Voucher Type"),
				"fieldtype": "Data",
				"fieldname": "voucher_type",
				"width": 100
			},
			{
				"label": _("Voucher No"),
				"fieldtype": "Dynamic Link",
				"fieldname": "voucher_no",
				"width": 140,
				"options": "voucher_type",
			}
		]

		if self.filters.get("party_type") != "Employee":
			columns.append({
				"label": _("Due Date"),
				"fieldtype": "Date",
				"fieldname": "due_date",
				"width": 80,
			})

		if self.filters.get("party_type") == "Supplier":
			columns += [
				{
					"label": _("Bill No"),
					"fieldtype": "Data",
					"fieldname": "bill_no",
					"width": 80
				},
				{
					"label": _("Bill Date"),
					"fieldtype": "Date",
					"fieldname": "bill_date",
					"width": 80,
				}
			]

		invoiced_label = "Invoiced Amount"
		paid_label = "Paid Amount"
		return_label = "Returned Amount"
		if self.filters.get("party_type") == "Customer":
			return_label = "Credit Note"
		elif self.filters.get("party_type") == "Supplier":
			return_label = "Debit Note"
		elif self.filters.get("party_type") == "Employee":
			invoiced_label = "Paid Amount"
			paid_label = "Claimed Amount"

		columns += [
			{
				"label": _(invoiced_label),
				"fieldname": "invoiced_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 120
			},
			{
				"label": _(paid_label),
				"fieldname": "paid_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 110
			},
			{
				"label": _(return_label),
				"fieldname": "return_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 110
			},
			{
				"label": _("Outstanding Amount"),
				"fieldname": "outstanding_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 120
			}
		]

		columns.append({
			"label": _("Age"),
			"fieldtype": "Int",
			"fieldname": "age",
			"width": 45,
		})

		if self.filters.get("party_type") == "Employee":
			columns.append({
				"label": _("Account"),
				"fieldtype": "Link",
				"fieldname": "account",
				"options": "Account",
				"width": 150
			})

		if self.has_cost_center:
			columns.append({
				"label": _("Cost Center"),
				"fieldtype": "Link",
				"fieldname": "cost_center",
				"options": "Cost Center",
				"width": 80,
				"hide_if_filtered": 1
			})

		if self.has_project:
			columns.append({
				"label": _("Project"),
				"fieldtype": "Link",
				"fieldname": "project",
				"options": "Project",
				"width": 85,
				"hide_if_filtered": 1
			})

		columns.append({
			"fieldname": "remarks",
			"label": _("Remarks"),
			"fieldtype": "Data",
			"width": 200
		})

		self.ageing_columns = self.get_ageing_columns()
		columns += self.ageing_columns

		columns += [
			{
				"fieldname": "currency",
				"label": _("Currency"),
				"fieldtype": "Link",
				"options": "Currency",
				"width": 50
			},
			{
				"fieldname": "pdc/lc_ref",
				"label": _("PDC/LC Ref"),
				"fieldtype": "Data",
				"width": 110
			},
			{
				"fieldname": "pdc/lc_amount",
				"label": _("PDC/LC Amount"),
				"fieldtype": "Currency",
				"options": "currency",
				"width": 130
			},
			{
				"fieldname": "remaining_balance",
				"label": _("Remaining Balance"),
				"fieldtype": "Currency",
				"options": "currency",
				"width": 130
			}
		]

		if self.filters.get('party_type') == 'Customer':
			columns += [
				{
					"label": _("PO #"),
					"fieldtype": "Data",
					"fieldname": "po_no",
					"width": 80,
				},
				{
					"fieldname": "delivery_note",
					"label": _("Delivery Note"),
					"fieldtype": "Link",
					"options": "Delivery Note",
					"width": 100
				},
				{
					"label": _("Sales Person"),
					"fieldtype": "Data",
					"fieldname": "sales_person",
					"width": 150,
				},
				{
					"fieldname": "territory",
					"label": _("Territory"),
					"fieldtype": "Link",
					"options": "Territory",
					"width": 100
				},
				{
					"fieldname": "customer_group",
					"label": _("Customer Group"),
					"fieldtype": "Link",
					"options": "Customer Group",
					"width": 100
				},
				{
					"label": _("Customer Contact"),
					"fieldtype": "Link",
					"fieldname": "contact",
					"options": "Contact",
					"width": 100
				}
			]
		if self.filters.get("party_type") == "Supplier":
			columns += [
				{
					"fieldname": "supplier_group",
					"label": _("Supplier Group"),
					"fieldtype": "Link",
					"options": "Supplier Group",
					"width": 120
				}
			]

		return columns

	def get_ageing_columns(self):
		ageing_columns = []
		lower_limit = 0
		for i, upper_limit in enumerate(self.ageing_range):
			ageing_columns.append({
				"label": "{0}-{1}".format(lower_limit, upper_limit),
				"fieldname": "range{}".format(i+1),
				"fieldtype": "Currency",
				"options": "currency",
				"ageing_column": 1,
				"width": 110
			})
			lower_limit = upper_limit + 1

		ageing_columns.append({
			"label": "{0}-Above".format(lower_limit),
			"fieldname": "range{}".format(self.ageing_column_count),
			"fieldtype": "Currency",
			"options": "currency",
			"ageing_column": 1,
			"width": 100
		})
		return ageing_columns


def execute(filters=None):
	args = {
		"party_type": "Customer",
		"naming_by": ["Selling Settings", "cust_master_name"],
	}
	return ReceivablePayableReport(filters).run(args)


def get_ageing_data(ageing_range, age_as_on, entry_date, outstanding_amount):
	outstanding_range = [0.0] * (len(ageing_range) + 1)

	if not (age_as_on and entry_date):
		return 0, outstanding_range

	age = (getdate(age_as_on) - getdate(entry_date)).days or 0
	index = None
	for i, days in enumerate(ageing_range):
		if age <= days:
			index = i
			break

	if index is None:
		index = len(ageing_range)

	outstanding_range[index] = outstanding_amount

	return age, outstanding_range


def get_advance_against_voucher_types():
	advance_against_voucher_types = ["Sales Order", "Purchase Order"]

	for voucher_types in frappe.get_hooks("advance_against_voucher_types"):
		if isinstance(voucher_types, str):
			voucher_types = [voucher_types]

		advance_against_voucher_types += voucher_types

	return advance_against_voucher_types
