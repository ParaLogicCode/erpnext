# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors and contributors
# For license information, please see license.txt
from copy import deepcopy

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.accounts.party import get_partywise_advance_payment_amount
from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport


class AccountsReceivableSummary(ReceivablePayableReport):
	def run(self, args):
		self.args = args
		self.validate_filters(args)

		data = self.get_data()
		columns = self.get_columns()

		return columns, data

	def get_columns(self):
		columns = [
			{
				"fieldname": "party",
				"label": _(self.filters.party_type),
				"fieldtype": "Link",
				"options": self.filters.party_type,
				"width": 80 if self.party_naming_by == "Naming Series" else 200
			}
		]

		if self.party_naming_by == "Naming Series":
			columns.append(
				{
					"fieldname": "party_name",
					"label": _(self.filters.party_type + " Name"),
					"fieldtype": "Data",
					"width": 200
				}
			)

		invoiced_label = "Invoiced Amount"
		paid_label = "Total Paid Amount"
		return_label = "Returned Amount"
		if self.filters.party_type == "Customer":
			return_label = "Credit Note Amount"
		elif self.filters.party_type == "Supplier":
			return_label = "Debit Note Amount"
		elif self.filters.party_type == "Employee":
			invoiced_label = "Paid Amount"
			paid_label = "Claimed Amount"

		columns += [
			{
				"label": _(invoiced_label),
				"fieldname": "invoiced_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 130
			},
			{
				"label": _(paid_label),
				"fieldname": "paid_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 140
			}
		]

		if self.filters.party_type != "Employee":
			columns.append({
				"label": _("Unallocated Advances"),
				"fieldname": "advance_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 140
			})

		columns += [
			{
				"label": _(return_label),
				"fieldname": "return_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 130
			},
			{
				"label": _("Outstanding Amount"),
				"fieldname": "outstanding_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 130
			}
		]

		self.ageing_columns = self.get_ageing_columns()
		columns += self.ageing_columns

		if self.filters.party_type == "Customer":
			columns += [
				{
					"label": _("Sales Person"),
					"fieldtype": "Data",
					"fieldname": "sales_person",
					"width": 120,
				},
				{
					"label": _("Territory"),
					"fieldname": "territory",
					"fieldtype": "Link",
					"options": "Territory",
					"width": 90
				},
				{
					"label": _("Customer Group"),
					"fieldname": "customer_group",
					"fieldtype": "Link",
					"options": "Customer Group",
					"width": 90
				},
			]

		if self.filters.party_type == "Supplier":
			columns += [{
				"label": _("Supplier Group"),
				"fieldname": "supplier_group",
				"fieldtype": "Link",
				"options": "Supplier Group",
				"width": 80
			}]

		columns.append({
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Link",
			"options": "Currency",
			"width": 70
		})

		return columns

	def get_data(self):
		partywise_total = self.get_partywise_total()
		self.get_party_map(list(partywise_total.keys()))

		partywise_advance_amount = frappe._dict()
		if self.filters.party_type != "Employee":
			partywise_advance_amount = get_partywise_advance_payment_amount(
				self.filters.company,
				self.filters.party_type,
				self.filters.get("report_date")
			) or frappe._dict()

		data = []
		for party, party_dict in partywise_total.items():
			row = frappe._dict()

			row["party"] = party
			row["party_name"] = self.get_party_name(party)

			row["advance_amount"] = partywise_advance_amount.get(party, 0)
			row["invoiced_amount"] = party_dict.invoiced_amount
			row["paid_amount"] = party_dict.paid_amount
			row["return_amount"] = party_dict.return_amount
			row["outstanding_amount"] = party_dict.outstanding_amount

			for i in range(self.ageing_column_count):
				row["range{0}".format(i+1)] = party_dict.get("range{0}".format(i+1))

			if self.filters.party_type == "Customer":
				row["territory"] = self.get_territory(party)
				row["customer_group"] = self.get_customer_group(party)
				row["sales_person"] = ", ".join(party_dict.sales_person)
			if self.filters.party_type == "Supplier":
				row["supplier_group"] = self.get_supplier_group(party)

			row["currency"] = party_dict.currency
			data.append(row)

		return data

	def get_partywise_total(self):
		party_total = frappe._dict()

		template = frappe._dict({
			"invoiced_amount": 0,
			"paid_amount": 0,
			"return_amount": 0,
			"outstanding_amount": 0,
			"sales_person": set()
		})
		for r in range(self.ageing_column_count):
			template['range{0}'.format(r+1)] = 0

		for d in self.get_voucherwise_data():
			if d.party not in party_total:
				party_total[d.party] = deepcopy(template)

			for k in list(party_total[d.party]):
				if k not in ["currency", "sales_person"]:
					party_total[d.party][k] += flt(d.get(k, 0))

			party_total[d.party].currency = d.currency

			if d.sales_person:
				party_total[d.party].sales_person.add(d.sales_person)

		return party_total

	def get_voucherwise_data(self):
		voucherwise_data = ReceivablePayableReport(self.filters).run(self.args)[1]
		return voucherwise_data


def execute(filters=None):
	args = {
		"party_type": "Customer",
		"naming_by": ["Selling Settings", "cust_master_name"],
	}

	return AccountsReceivableSummary(filters).run(args)
