# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe, erpnext
import json
from frappe import _, throw, scrub
from frappe.utils import (today, flt, cint, fmt_money, formatdate, cstr, date_diff,
	getdate, add_days, add_months, get_last_day, nowdate, get_link_to_form, clean_whitespace)
from frappe.model.workflow import get_workflow_name, is_transition_condition_satisfied, WorkflowPermissionError
from erpnext.stock.get_item_details import get_conversion_factor, get_item_details, get_applies_to_details
from erpnext.setup.utils import get_exchange_rate
from erpnext.accounts.utils import get_fiscal_years, validate_fiscal_year, get_account_currency
from erpnext.utilities.transaction_base import TransactionBase
from erpnext.buying.utils import update_last_purchase_rate
from erpnext.controllers.sales_and_purchase_return import validate_return
from erpnext.accounts.party import get_party_account_currency, validate_party_frozen_disabled
from erpnext.accounts.doctype.pricing_rule.utils import (apply_pricing_rule_on_transaction,
	apply_pricing_rule_for_free_items, get_applied_pricing_rules, update_pricing_rule_table)
from erpnext.exceptions import InvalidCurrency
from six import text_type
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
from erpnext.stock.get_item_details import get_default_warehouse
from erpnext.stock.doctype.packed_item.packed_item import make_packing_list
from collections import OrderedDict

force_item_fields = ("item_group", "brand", "stock_uom", "alt_uom", "alt_uom_size",
	"item_tax_rate", "pricing_rules", "allow_zero_valuation_rate", "force_default_warehouse",
	"is_stock_item", "is_fixed_asset" "has_batch_no", "has_serial_no", "is_vehicle",
	"claim_customer", "sales_commission_category", "commission_rate", "retail_rate")

force_party_fields = ("customer_name", "bill_to_name", "supplier_name",
	"customer_group", "supplier_group",
	"contact_display", "contact_mobile", "contact_phone", "contact_email",
	"address_display", "company_address_display",
	"customer_credit_limit", "customer_credit_balance", "customer_outstanding_amount", "previous_outstanding_amount",
	"tax_id", "tax_cnic", "tax_strn",
	"retail_price_list")

force_applies_to_fields = ("vehicle_chassis_no", "vehicle_engine_no", "vehicle_license_plate", "vehicle_unregistered",
	"vehicle_color", "vehicle_last_odometer", "applies_to_item", "vehicle_owner_name", "vehicle_warranty_no")

merge_items_sum_fields = ['qty', 'stock_qty', 'alt_uom_qty', 'net_weight',
	'amount', 'taxable_amount', 'net_amount', 'total_discount', 'amount_before_discount',
	'item_taxes', 'item_taxes_before_discount', 'tax_inclusive_amount', 'tax_inclusive_amount_before_discount',
	'amount_before_depreciation', 'depreciation_amount']

merge_items_rate_fields = [('rate', 'amount'), ('taxable_rate', 'taxable_amount'), ('net_rate', 'net_amount'),
	('discount_amount', 'total_discount'), ('price_list_rate', 'amount_before_discount'),
	('tax_inclusive_rate', 'tax_inclusive_amount'),
	('tax_inclusive_rate_before_discount', 'tax_inclusive_amount_before_discount'),
	('net_weight_per_unit', 'net_weight'),
]

print_total_fields_from_items = [
	('total_qty', 'qty'),
	('total_alt_uom_qty', 'alt_uom_qty'),

	('total', 'amount'),
	('tax_exclusive_total', 'tax_exclusive_amount'),
	('retail_total', 'retail_amount'),
	('net_total', 'net_amount'),
	('taxable_total', 'taxable_amount'),

	('total_discount', 'total_discount'),
	('tax_exclusive_total_discount', 'tax_exclusive_total_discount'),
	('total_before_discount', 'amount_before_discount'),
	('tax_exclusive_total_before_discount', 'tax_exclusive_amount_before_discount'),

	('total_depreciation', 'depreciation_amount'),
	('tax_exclusive_total_depreciation', 'tax_exclusive_depreciation_amount'),
	('total_before_depreciation', 'amount_before_depreciation'),
	('tax_exclusive_total_before_depreciation', 'tax_exclusive_amount_before_depreciation'),

	('grand_total', 'tax_inclusive_amount'),
	('grand_total_before_discount', 'tax_inclusive_amount_before_discount'),
	('total_taxes_and_charges', 'item_taxes'),
	('total_taxes_and_charges_before_discount', 'item_taxes_before_discount'),

	('total_net_weight', 'net_weight')
]


class AccountsController(TransactionBase):
	def __init__(self, *args, **kwargs):
		super(AccountsController, self).__init__(*args, **kwargs)

	@property
	def company_currency(self):
		if not hasattr(self, "__company_currency"):
			self.__company_currency = erpnext.get_company_currency(self.company)

		return self.__company_currency

	def onload(self):
		self.set_onload("make_payment_via_journal_entry",
			frappe.db.get_single_value('Accounts Settings', 'make_payment_via_journal_entry'))

		if self.is_new():
			relevant_docs = ("Quotation", "Purchase Order", "Sales Order",
							 "Purchase Invoice", "Sales Invoice")
			if self.doctype in relevant_docs:
				self.set_payment_schedule()

		self.set_onload("enable_dynamic_bundling", self.dynamic_bundling_enabled())

		if self.docstatus == 0:
			self.set_missing_values()

	def dynamic_bundling_enabled(self):
		return self.doctype in ['Quotation', 'Sales Order', 'Delivery Note', 'Sales Invoice'] and\
			frappe.get_cached_value('Stock Settings', None, "enable_dynamic_bundling")

	def ensure_supplier_is_not_blocked(self):
		is_supplier_payment = self.doctype == 'Payment Entry' and self.party_type == 'Supplier'
		is_buying_invoice = self.doctype in ['Purchase Invoice', 'Purchase Order', 'Landed Cost Voucher',
			'Vehicle Booking Order', 'Vehicle Receipt', 'Vehicle Registration Order']

		supplier_name = self.get('party') if self.get('party') and self.get('party_type') == "Supplier" else self.get('supplier')
		if self.doctype == "Vehicle Registration Order":
			supplier_name = self.get('agent')

		supplier_doc = None
		if supplier_name:
			supplier_doc = frappe.get_doc('Supplier', supplier_name)

		if supplier_doc and supplier_name and supplier_doc.on_hold:
			if (is_buying_invoice and supplier_doc.hold_type in ['All', 'Invoices']) or \
					(is_supplier_payment and supplier_doc.hold_type in ['All', 'Payments']):
				if not supplier_doc.release_date or getdate(nowdate()) <= supplier_doc.release_date:
					frappe.msgprint(
						_('{0} is blocked so this transaction cannot proceed'.format(supplier_name)), raise_exception=1)

	def validate(self):
		self.validate_qty_is_not_zero()

		if self.get("_action") and self._action != "update_after_submit":
			self.set_missing_values(for_validate=True)

		self.ensure_supplier_is_not_blocked()

		self.validate_date_with_fiscal_year()

		if self.meta.get_field("currency"):
			self.calculate_taxes_and_totals()

			if not self.meta.get_field("is_return") or not self.is_return:
				self.validate_value("base_grand_total", ">=", 0)
			if self.get('is_return'):
				self.validate_value("base_grand_total", "<=", 0)

			validate_return(self)

			if self.meta.has_field('in_words'):
				self.set_total_in_words()

		self.validate_all_documents_schedule()

		if self.meta.get_field("taxes_and_charges"):
			self.validate_enabled_taxes_and_charges()
			self.validate_tax_account_company()

		self.validate_party()
		self.validate_currency()

		self.clean_remarks()

		if self.doctype == 'Purchase Invoice':
			self.calculate_paid_amount()

		if self.doctype in ['Purchase Invoice', 'Sales Invoice']:
			pos_check_field = "is_pos" if self.doctype=="Sales Invoice" else "is_paid"
			if cint(self.allocate_advances_automatically) and not cint(self.get(pos_check_field)):
				self.set_advances()

			if not cint(self.get(pos_check_field)) and not self.is_return:
				self.validate_advance_entries()

			if not self.is_return:
				self.validate_deferred_start_and_end_date()

		elif self.doctype in ['Landed Cost Voucher'] and cint(self.allocate_advances_automatically):
			self.set_advances()

		validate_regional(self)
		if self.doctype != 'Material Request':
			apply_pricing_rule_on_transaction(self)
			update_pricing_rule_table(self)

	def validate_deferred_start_and_end_date(self):
		for d in self.items:
			if d.get("enable_deferred_revenue") or d.get("enable_deferred_expense"):
				if not (d.service_start_date and d.service_end_date):
					frappe.throw(_("Row #{0}: Service Start and End Date is required for deferred accounting").format(d.idx))
				elif getdate(d.service_start_date) > getdate(d.service_end_date):
					frappe.throw(_("Row #{0}: Service Start Date cannot be greater than Service End Date").format(d.idx))
				elif getdate(self.posting_date) > getdate(d.service_end_date):
					frappe.throw(_("Row #{0}: Service End Date cannot be before Invoice Posting Date").format(d.idx))

	def validate_invoice_documents_schedule(self):
		self.set_payment_schedule()
		self.validate_payment_schedule_dates()
		self.set_due_date()
		self.validate_payment_schedule_amount()
		self.validate_due_date()

	def validate_non_invoice_documents_schedule(self):
		self.set_payment_schedule()
		self.validate_payment_schedule_dates()
		self.validate_payment_schedule_amount()

	def validate_all_documents_schedule(self):
		if self.doctype in ("Sales Invoice", "Purchase Invoice") and not self.is_return:
			self.validate_invoice_documents_schedule()
		elif self.doctype in ("Quotation", "Purchase Order", "Sales Order"):
			self.validate_non_invoice_documents_schedule()

	def before_print(self, print_settings=None):
		if self.doctype == "Sales Invoice":
			self.delivery_notes = list(set(item.delivery_note for item in self.items if item.get("delivery_note")))
		if self.doctype == "Purchase Invoice":
			self.purchase_receipts = list(set(item.purchase_receipt for item in self.items if item.get("purchase_receipt")))
		if self.doctype in ["Sales Invoice", "Delivery Note", "Material Request"]:
			self.sales_orders = list(set(item.sales_order for item in self.items if item.get('sales_order')))
		if self.doctype in ["Purchase Invoice", "Purchase Receipt"]:
			self.purchase_orders = list(set(item.purchase_order for item in self.items if item.get('purchase_order')))
		if self.doctype == "Sales Order":
			self.quotations = list(set(item.quotation for item in self.items if item.get("quotation")))
		if self.doctype == "Purchase Order":
			self.supplier_quotations = list(set(item.supplier_quotation for item in self.items if item.get("supplier_quotation")))

		if self.doctype in ['Purchase Order', 'Sales Order', 'Sales Invoice', 'Purchase Invoice',
							'Supplier Quotation', 'Purchase Receipt', 'Delivery Note', 'Quotation']:
			if self.dynamic_bundling_enabled():
				self.merge_bundled_items()

			if self.get("group_same_items"):
				self.group_similar_items()

			self.uom = self.get_common_uom(self.get("items"))
			self.stock_uom = self.get_common_uom(self.get("items"), "stock_uom")
			self.items_by_item_group = self.group_items_by_item_group(self.items)
			self.items_by_item_tax_and_item_group = self.group_items_by_item_tax_and_item_group()

			self.calculate_tax_rates_for_print()

			self.warehouses = list(set([frappe.get_cached_value("Warehouse", item.warehouse, 'warehouse_name')
				for item in self.items if item.get('warehouse')]))

			for item in self.items:
				item.alt_uom_or_uom = item.alt_uom or item.uom

			if self.get("discount_amount"):
				self.discount_amount = -self.discount_amount

			if self.get("total_discount_after_taxes"):
				self.total_discount_after_taxes = -self.total_discount_after_taxes

			df = self.meta.get_field("discount_amount")
			if self.get("discount_amount") and hasattr(self, "taxes") and not len(self.taxes):
				df.set("print_hide", 0)
			else:
				df.set("print_hide", 1)

		if self.doctype in ['Journal Entry', 'Payment Entry']:
			self.get_gl_entries_for_print()
			self.get_party_to_party_name_dict()
			self.get_vehicle_details_map()

		self.company_address_doc = erpnext.get_company_address(self)

		if self.doctype == "Stock Entry":
			self.s_warehouses = list(set([frappe.get_cached_value("Warehouse", item.s_warehouse, 'warehouse_name')
				for item in self.items if item.get('s_warehouse')]))
			self.t_warehouses = list(set([frappe.get_cached_value("Warehouse", item.t_warehouse, 'warehouse_name')
				for item in self.items if item.get('t_warehouse')]))

		if self.doctype == "Sales Invoice":
			self.payments = self.get('payments', {'amount': ['!=', 0]})

	def calculate_paid_amount(self):
		if hasattr(self, "is_pos") or hasattr(self, "is_paid"):
			is_paid = self.get("is_pos") or self.get("is_paid")

			if is_paid:
				if not self.cash_bank_account:
					# show message that the amount is not paid
					frappe.throw(_("Note: Payment Entry will not be created since 'Cash or Bank Account' was not specified"))

				if cint(self.is_return) and self.grand_total > self.paid_amount:
					self.paid_amount = flt(flt(self.grand_total), self.precision("paid_amount"))
				elif not flt(self.paid_amount) and flt(self.outstanding_amount) > 0:
					self.paid_amount = flt(flt(self.outstanding_amount), self.precision("paid_amount"))

				self.base_paid_amount = flt(self.paid_amount * self.conversion_rate, self.precision("base_paid_amount"))
			else:
				self.paid_amount = 0
				self.base_paid_amount = 0

	def set_missing_values(self, for_validate=False):
		if frappe.flags.in_test:
			for fieldname in ["posting_date", "transaction_date"]:
				if self.meta.get_field(fieldname) and not self.get(fieldname):
					self.set(fieldname, today())
					break

		self.set_project_reference_no()

	def calculate_taxes_and_totals(self):
		from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
		calculate_taxes_and_totals(self)

		if self.doctype in ["Quotation", "Sales Order", "Delivery Note", "Sales Invoice"]:
			self.calculate_commission()
			self.calculate_sales_team_contribution(self.get('base_net_total'))

	def validate_date_with_fiscal_year(self):
		if self.meta.get_field("fiscal_year"):
			date_field = ""
			if self.meta.get_field("posting_date"):
				date_field = "posting_date"
			elif self.meta.get_field("transaction_date"):
				date_field = "transaction_date"

			if date_field and self.get(date_field):
				validate_fiscal_year(self.get(date_field), self.fiscal_year, self.company,
									 self.meta.get_label(date_field), self)

	def validate_due_date(self):
		if self.get('is_pos'): return

		from erpnext.accounts.party import validate_due_date
		if self.doctype in ("Sales Invoice", "Vehicle Booking Order"):
			if not self.due_date:
				frappe.throw(_("Due Date is mandatory"))

			validate_due_date(self.get('posting_date') or self.get('transaction_date'), self.due_date,
				"Customer", self.customer, self.company, self.payment_terms_template)
		elif self.doctype == "Purchase Invoice":
			validate_due_date(self.bill_date or self.posting_date, self.due_date,
				"Supplier", self.supplier, self.company, self.bill_date, self.payment_terms_template)

	def validate_quotation_valid_till(self):
		if cint(self.quotation_validity_days) < 0:
			frappe.throw(_("Quotation Validity Days cannot be negative"))

		if cint(self.quotation_validity_days):
			self.valid_till = add_days(getdate(self.transaction_date), cint(self.quotation_validity_days) - 1)
		if not cint(self.quotation_validity_days) and self.valid_till:
			self.quotation_validity_days = date_diff(self.valid_till, self.transaction_date) + 1

		if self.valid_till and getdate(self.valid_till) < getdate(self.transaction_date):
			frappe.throw(_("Valid till date cannot be before transaction date"))

	def set_price_list_currency(self, buying_or_selling):
		if self.meta.get_field("posting_date"):
			transaction_date = self.posting_date
		else:
			transaction_date = self.transaction_date

		if self.meta.get_field("currency"):
			# price list part
			if buying_or_selling.lower() == "selling":
				fieldname = "selling_price_list"
				args = "for_selling"
			else:
				fieldname = "buying_price_list"
				args = "for_buying"

			if self.meta.get_field(fieldname) and self.get(fieldname):
				self.price_list_currency = frappe.db.get_value("Price List",
															   self.get(fieldname), "currency")

				if self.price_list_currency == self.company_currency:
					self.plc_conversion_rate = 1.0

				elif not self.plc_conversion_rate:
					self.plc_conversion_rate = get_exchange_rate(self.price_list_currency,
																 self.company_currency, transaction_date, args)

			# currency
			if not self.currency:
				self.currency = self.price_list_currency
				self.conversion_rate = self.plc_conversion_rate
			elif self.currency == self.company_currency:
				self.conversion_rate = 1.0
			elif not self.conversion_rate:
				self.conversion_rate = get_exchange_rate(self.currency,
														 self.company_currency, transaction_date, args)

	def get_item_details_parent_args(self):
		parent_dict = {}
		for fieldname in self.meta.get_valid_columns():
			parent_dict[fieldname] = self.get(fieldname)

		if self.doctype in ["Quotation", "Sales Order", "Delivery Note", "Sales Invoice"]:
			document_type = "{} Item".format(self.doctype)
			parent_dict.update({"document_type": document_type})

		if 'transaction_type' in parent_dict:
			parent_dict['transaction_type_name'] = parent_dict.pop('transaction_type')

		# party_name field used for customer in quotation
		if self.doctype == "Quotation" and self.quotation_to == "Customer" and parent_dict.get("party_name"):
			parent_dict.update({"customer": parent_dict.get("party_name")})

		return parent_dict

	def get_item_details_child_args(self, item, parent_dict):
		args = parent_dict.copy()
		args.update(item.as_dict())

		args["doctype"] = self.doctype
		args["name"] = self.name
		args["child_docname"] = item.name

		if not args.get("transaction_date"):
			args["transaction_date"] = args.get("posting_date")

		if self.get("is_subcontracted"):
			args["is_subcontracted"] = self.is_subcontracted

		return args

	def set_missing_item_details(self, for_validate=False):
		"""set missing item values"""
		from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

		if hasattr(self, "items"):
			parent_dict = self.get_item_details_parent_args()

			for item in self.get("items"):
				if item.get("item_code"):
					args = self.get_item_details_child_args(item, parent_dict)
					ret = get_item_details(args, self, for_validate=True, overwrite_warehouse=False)

					for fieldname, value in ret.items():
						if item.meta.get_field(fieldname) and value is not None:
							if item.get(fieldname) is None or fieldname in force_item_fields:
								item.set(fieldname, value)

							elif fieldname in ['cost_center', 'conversion_factor'] and not item.get(fieldname):
								item.set(fieldname, value)

							elif fieldname == "serial_no":
								# Ensure that serial numbers are matched against Stock UOM
								item_conversion_factor = item.get("conversion_factor") or 1.0
								item_qty = abs(item.get("qty")) * item_conversion_factor

								if item_qty != len(get_serial_nos(item.get('serial_no'))):
									item.set(fieldname, value)

					if self.doctype in ["Purchase Invoice", "Sales Invoice"] and item.meta.get_field('is_fixed_asset'):
						item.set('is_fixed_asset', ret.get('is_fixed_asset', 0))

					if ret.get("pricing_rules"):
						self.apply_pricing_rule_on_items(item, ret)

			if self.doctype == "Purchase Invoice":
				self.set_expense_account(for_validate)

		self.set_missing_applies_to_details()

	def set_missing_applies_to_details(self):
		if not self.meta.has_field('applies_to_item'):
			return

		if self.get("applies_to_vehicle"):
			self.applies_to_serial_no = self.applies_to_vehicle

		args = self.get_item_details_parent_args()
		applies_to_details = get_applies_to_details(args, for_validate=True)

		for k, v in applies_to_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in force_applies_to_fields:
				self.set(k, v)

		from erpnext.vehicles.utils import format_vehicle_fields
		format_vehicle_fields(self)

	def apply_pricing_rule_on_items(self, item, pricing_rule_args):
		if not pricing_rule_args.get("validate_applied_rule", 0):
			# if user changed the discount percentage then set user's discount percentage ?
			if pricing_rule_args.get("price_or_product_discount") == 'Price':
				item.set("pricing_rules", pricing_rule_args.get("pricing_rules"))
				item.set("discount_percentage", pricing_rule_args.get("discount_percentage"))
				item.set("discount_amount", pricing_rule_args.get("discount_amount"))
				if pricing_rule_args.get("pricing_rule_for") == "Rate":
					item.set("price_list_rate", pricing_rule_args.get("price_list_rate"))

				if item.get("price_list_rate"):
					item.rate = flt(item.price_list_rate *
						(1.0 - (flt(item.discount_percentage) / 100.0)), item.precision("rate"))

					if item.get('discount_amount'):
						item.rate = item.price_list_rate - item.discount_amount

			elif pricing_rule_args.get('free_item_data'):
				apply_pricing_rule_for_free_items(self, pricing_rule_args.get('free_item_data'))

		elif pricing_rule_args.get("validate_applied_rule"):
			for pricing_rule in get_applied_pricing_rules(item.get('pricing_rules')):
				pricing_rule_doc = frappe.get_cached_doc("Pricing Rule", pricing_rule)
				for field in ['discount_percentage', 'discount_amount', 'rate']:
					if item.get(field) < pricing_rule_doc.get(field):
						title = get_link_to_form("Pricing Rule", pricing_rule)

						frappe.msgprint(_("Row {0}: user has not applied the rule {1} on the item {2}")
							.format(item.idx, frappe.bold(title), frappe.bold(item.item_code)))

	def reset_taxes_and_charges(self):
		if not self.meta.get_field("taxes"):
			return

		self.set("taxes", [])
		self.set_taxes_and_charges()

	def set_taxes_and_charges(self):
		if not self.meta.get_field("taxes"):
			return

		tax_master_doctype = self.meta.get_field("taxes_and_charges").options

		if (self.is_new() or self.is_pos_profile_changed()) and not self.get("taxes"):
			if self.company and not self.get("taxes_and_charges"):
				# get the default tax master
				self.taxes_and_charges = frappe.db.get_value(tax_master_doctype, {"is_default": 1, 'company': self.company})

			self.append_taxes_from_master(tax_master_doctype)

	def append_taxes_from_master(self, tax_master_doctype=None):
		if self.get("taxes_and_charges"):
			if not tax_master_doctype:
				tax_master_doctype = self.meta.get_field("taxes_and_charges").options

			self.extend("taxes", get_taxes_and_charges(tax_master_doctype, self.get("taxes_and_charges")))

	def is_pos_profile_changed(self):
		if (self.doctype == 'Sales Invoice' and self.is_pos and
				self.pos_profile != frappe.db.get_value('Sales Invoice', self.name, 'pos_profile')):
			return True

	def validate_enabled_taxes_and_charges(self):
		taxes_and_charges_doctype = self.meta.get_options("taxes_and_charges")
		if self.taxes_and_charges and frappe.get_cached_value(taxes_and_charges_doctype, self.taxes_and_charges, "disabled"):
			frappe.throw(_("{0} '{1}' is disabled").format(taxes_and_charges_doctype, self.taxes_and_charges))

	def validate_tax_account_company(self):
		for d in self.get("taxes"):
			if d.account_head:
				tax_account_company = frappe.get_cached_value("Account", d.account_head, "company")
				if tax_account_company != self.company:
					frappe.throw(_("Row #{0}: Account {1} does not belong to company {2}")
								 .format(d.idx, d.account_head, self.company))

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

	def validate_qty_is_not_zero(self):
		if self.get('is_return') and self.doctype in ("Sales Invoice", "Purchase Invoice") and not self.update_stock:
			return

		for item in self.items:
			if not item.qty and not item.get('rejected_qty'):
				frappe.throw(_("Row #{0}: Item Quantity can not be zero").format(item.idx))

	def validate_account_currency(self, account, account_currency=None):
		valid_currency = [self.company_currency]
		if self.get("currency") and self.currency != self.company_currency:
			valid_currency.append(self.currency)

		if account_currency not in valid_currency:
			frappe.throw(_("Account {0} is invalid. Account Currency must be {1}")
						 .format(account, _(" or ").join(valid_currency)))

	def clear_unallocated_advances(self, childtype, parentfield):
		self.set(parentfield, self.get(parentfield, {"allocated_amount": ["not in", [0, None, ""]]}))

		frappe.db.sql("""delete from `tab%s` where parentfield=%s and parent = %s
			and allocated_amount = 0""" % (childtype, '%s', '%s'), (parentfield, self.name))

	@frappe.whitelist()
	def apply_shipping_rule(self):
		if self.shipping_rule:
			shipping_rule = frappe.get_doc("Shipping Rule", self.shipping_rule)
			shipping_rule.apply(self)
			self.calculate_taxes_and_totals()

	def get_shipping_address(self):
		'''Returns Address object from shipping address fields if present'''

		# shipping address fields can be `shipping_address_name` or `shipping_address`
		# try getting value from both

		for fieldname in ('shipping_address_name', 'shipping_address'):
			shipping_field = self.meta.get_field(fieldname)
			if shipping_field and shipping_field.fieldtype == 'Link':
				if self.get(fieldname):
					return frappe.get_doc('Address', self.get(fieldname))

		return {}

	@frappe.whitelist()
	def set_advances(self):
		"""Returns list of advances against Account, Party, Reference"""

		res = self.get_advance_entries()
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

	def get_advance_entries(self, include_unallocated=True):
		against_all_orders = False
		order_field = None
		order_doctype = None
		if self.doctype == "Sales Invoice":
			party_account = self.debit_to
			party_type, party = self.get_billing_party()
			order_field = "sales_order"
			order_doctype = "Sales Order"
		elif self.doctype == "Purchase Invoice":
			party_account = self.credit_to
			party_type, party = self.get_billing_party()
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

	def is_inclusive_tax(self):
		is_inclusive = cint(frappe.get_cached_value("Accounts Settings", None, "show_inclusive_tax_in_print"))

		if is_inclusive:
			is_inclusive = 0
			if self.get("taxes", filters={"included_in_print_rate": 1}):
				is_inclusive = 1

		return is_inclusive

	def validate_advance_entries(self):
		order_field = "sales_order" if self.doctype == "Sales Invoice" else "purchase_order"
		order_list = list(set([d.get(order_field)
			for d in self.get("items") if d.get(order_field)]))

		if not order_list: return

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
			party_type, party = self.get_billing_party()
			party_account = self.debit_to
			dr_or_cr = "credit_in_account_currency"
		elif self.doctype == "Purchase Invoice":
			party_type, party = self.get_billing_party()
			party_account = self.credit_to
			dr_or_cr = "debit_in_account_currency"
		elif self.doctype == "Expense Claim":
			party_type = "Employee"
			party = self.employee
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
					'exchange_rate': (self.conversion_rate
						if self.party_account_currency != self.company_currency else 1),
					'grand_total': (self.base_grand_total
						if self.party_account_currency == self.company_currency else self.grand_total),
					'outstanding_amount': self.outstanding_amount
				})
				args.update(invoice_amounts)
				lst.append(args)

		if lst:
			from erpnext.accounts.utils import reconcile_against_document
			reconcile_against_document(lst)

	def on_cancel(self):
		from erpnext.accounts.utils import unlink_ref_doc_from_payment_entries

		if self.doctype in ["Sales Invoice", "Purchase Invoice", "Landed Cost Voucher", "Expense Claim"]:
			if self.get('is_return'):
				return

			if frappe.db.get_single_value('Accounts Settings', 'unlink_payment_on_cancellation_of_invoice'):
				unlink_ref_doc_from_payment_entries(self, True)

		elif self.doctype in ["Sales Order", "Purchase Order"]:
			if frappe.db.get_single_value('Accounts Settings', 'unlink_advance_payment_on_cancelation_of_order'):
				unlink_ref_doc_from_payment_entries(self, True)

	def update_item_prices(self):
		from erpnext.stock.get_item_details import get_price_list_rate, process_args
		from erpnext.stock.report.item_prices.item_prices import _set_item_pl_rate

		parent_dict = frappe._dict({})
		for fieldname in self.meta.get_valid_columns():
			parent_dict[fieldname] = self.get(fieldname)

		if self.doctype in ["Quotation", "Sales Order", "Delivery Note", "Sales Invoice"]:
			document_type = "{} Item".format(self.doctype)
			parent_dict.update({"document_type": document_type})

		for item in self.get("items"):
			if item.get("item_code"):
				args = parent_dict.copy()
				args.update(item.as_dict())
				args = process_args(args)

				args["doctype"] = self.doctype
				args["name"] = self.name

				if not args.get("transaction_date"):
					args["transaction_date"] = args.get("posting_date")

				item_price_data = frappe._dict()
				get_price_list_rate(args, frappe.get_cached_doc("Item", item.item_code), item_price_data)

				if flt(item.price_list_rate) and abs(flt(item_price_data.price_list_rate) - flt(item.price_list_rate)) > 0.005:
					_set_item_pl_rate(args["transaction_date"], item.item_code, self.buying_price_list, flt(item.price_list_rate), item.uom, item.conversion_factor)

	def get_company_default(self, fieldname):
		from erpnext.accounts.utils import get_company_default
		return get_company_default(self.company, fieldname)

	def get_stock_items(self, item_codes=None):
		stock_items = []

		if item_codes is None:
			item_codes = [item.item_code for item in self.get("items")]

		if item_codes:
			item_codes = list(set(item_codes))

			stock_items = frappe.db.sql_list("""
				select name
				from `tabItem`
				where name in %s and is_stock_item = 1
			""", [item_codes])

		return stock_items

	def set_total_advance_paid(self):
		if self.doctype == "Sales Order":
			dr_or_cr = "credit_in_account_currency - debit_in_account_currency"
			party = self.customer
		else:
			dr_or_cr = "debit_in_account_currency - credit_in_account_currency"
			party = self.supplier

		advance = frappe.db.sql("""
			select
				account_currency, sum({dr_or_cr}) as amount
			from
				`tabGL Entry`
			where
				((against_voucher_type = %(doctype)s and against_voucher = %(name)s)
					or (original_against_voucher_type = %(doctype)s and original_against_voucher = %(name)s))
				and party=%(party)s
		""".format(dr_or_cr=dr_or_cr), {
			"doctype": self.doctype, "name": self.name, "party": party
		}, as_dict=1)

		if advance:
			advance = advance[0]
			advance_paid = flt(advance.amount, self.precision("advance_paid"))
			formatted_advance_paid = fmt_money(advance_paid, precision=self.precision("advance_paid"),
											   currency=advance.account_currency)

			frappe.db.set_value(self.doctype, self.name, "party_account_currency",
								advance.account_currency)

			if advance.account_currency == self.currency:
				order_total = self.get("rounded_total") or self.grand_total
				precision = "rounded_total" if self.get("rounded_total") else "grand_total"
			else:
				order_total = self.get("base_rounded_total") or self.base_grand_total
				precision = "base_rounded_total" if self.get("base_rounded_total") else "base_grand_total"

			formatted_order_total = fmt_money(order_total, precision=self.precision(precision),
											  currency=advance.account_currency)

			if self.currency == self.company_currency and advance_paid > order_total:
				frappe.throw(_("Total advance ({0}) against Order {1} cannot be greater than the Grand Total ({2})")
							 .format(formatted_advance_paid, self.name, formatted_order_total))

			self.db_set("advance_paid", advance_paid)
			self.notify_update()

	@property
	def company_abbr(self):
		if not hasattr(self, "_abbr"):
			self._abbr = frappe.db.get_value('Company',  self.company,  "abbr")

		return self._abbr

	def validate_party(self):
		party_type, party = self.get_party()
		validate_party_frozen_disabled(party_type, party)

		billing_party_type, billing_party = self.get_billing_party()
		if (billing_party_type, billing_party) != (party_type, party):
			validate_party_frozen_disabled(billing_party_type, billing_party)

	def get_party(self, with_name=False):
		if self.doctype == "Landed Cost Voucher":
			return self.get("party_type"), self.get("party")

		party_type = None
		if self.doctype in ("Opportunity", "Quotation", "Sales Order", "Delivery Note", "Sales Invoice"):
			party_type = 'Customer'

		elif self.doctype in ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice"):
			party_type = 'Supplier'

		elif self.meta.get_field("customer"):
			party_type = "Customer"

		elif self.meta.get_field("supplier"):
			party_type = "Supplier"

		party = self.get(scrub(party_type)) if party_type else None
		party_name = self.get(scrub(party_type) + "_name") if party_type else None

		if with_name:
			return party_type, party, party_name
		else:
			return party_type, party

	def get_billing_party(self, with_name=False):
		if self.get("bill_to"):
			party_type, party = self.get_party()
			if with_name:
				return party_type, self.get("bill_to"), self.get("bill_to_name")
			else:
				return party_type, self.get("bill_to")
		if self.get("letter_of_credit"):
			if with_name:
				return "Letter of Credit", self.get("letter_of_credit"), self.get("letter_of_credit")
			else:
				return "Letter of Credit", self.get("letter_of_credit")
		else:
			return self.get_party(with_name=with_name)

	def validate_currency(self):
		if self.get("currency"):
			party_type, party = self.get_billing_party()
			if party_type and party:
				party_account_currency = get_party_account_currency(party_type, party, self.company)

				if (party_account_currency
						and party_account_currency != self.company_currency
						and self.currency != party_account_currency):
					frappe.throw(_("Accounting Entry for {0}: {1} can only be made in currency: {2}")
								 .format(party_type, party, party_account_currency), InvalidCurrency)

				# Note: not validating with gle account because we don't have the account
				# at quotation / sales order level and we shouldn't stop someone
				# from creating a sales invoice if sales order is already created

	def clean_remarks(self):
		fields = [
			'remarks', 'remark', 'user_remark', 'user_remarks',
			'cheque_no', 'reference_no',
			'po_no', 'supplier_delivery_note', 'lr_no'
		]
		for f in fields:
			if self.meta.has_field(f):
				self.set(f, clean_whitespace(self.get(f)))

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

		frappe.db.set_value(self.doctype, self.name, "total_advance",
							total_allocated_amount, update_modified=False)

	def merge_bundled_items(self):
		bundles = {}
		item_meta = frappe.get_meta(self.doctype + " Item")
		count = 0

		copy_fields = ['qty', 'stock_qty', 'alt_uom_qty']

		sum_fields = merge_items_sum_fields.copy()
		sum_fields = [f for f in sum_fields if f not in copy_fields]
		sum_fields += ['tax_exclusive_' + f for f in sum_fields if item_meta.has_field('tax_exclusive_' + f)]

		rate_fields = merge_items_rate_fields.copy()
		rate_fields += [('tax_exclusive_' + t, 'tax_exclusive_' + s) for t, s in rate_fields
			if item_meta.has_field('tax_exclusive_' + t) and item_meta.has_field('tax_exclusive_' + s)]

		base_fields = [('base_' + f, f) for f in sum_fields if item_meta.has_field('base_' + f)]
		base_fields += [('base_' + t, t) for t, s in rate_fields if item_meta.has_field('base_' + t)]
		base_fields += [('base_' + f, f) for f in copy_fields if item_meta.has_field('base_' + f)]

		# Sum amounts
		in_bundle = 0
		for item in self.items:
			if item.get('bundling_state') == 'Start':
				in_bundle = item.idx

			if not in_bundle or item.get('bundling_state') == 'Start':
				new_bundle = frappe._dict()
				for f in copy_fields:
					new_bundle[f] = item.get(f)
				bundles[item.idx] = new_bundle

			group_item = bundles[in_bundle or item.idx]

			if item.get('bundling_state') == 'Terminate':
				in_bundle = 0

			self.merge_similar_item_aggregate(item, group_item, sum_fields)

		# Calculate average rates and get serial nos string
		for group_item in bundles.values():
			self.merge_similar_items_postprocess(group_item, rate_fields)

		# Calculate company currency values
		for group_item in bundles.values():
			for target, source in base_fields:
				group_item[target] = group_item.get(source, 0) * self.conversion_rate

		# Remove duplicates and set aggregated values
		to_remove = []
		for item in self.items:
			if item.idx in bundles.keys():
				count += 1
				item.update(bundles[item.idx])
				del bundles[item.idx]
				item.idx = count
			else:
				to_remove.append(item)

		for item in to_remove:
			self.remove(item)

		self.total_qty = sum([d.qty for d in self.items])
		self.total_alt_uom_qty = sum([d.alt_uom_qty for d in self.items])

	def group_similar_items(self, additional_sum_fields=None, additional_rate_fields=None):
		group_item_data = {}
		item_meta = frappe.get_meta(self.doctype + " Item")
		count = 0

		sum_fields = merge_items_sum_fields.copy() + (additional_sum_fields or [])
		sum_fields += ['tax_exclusive_' + f for f in sum_fields if item_meta.has_field('tax_exclusive_' + f)]

		rate_fields = merge_items_rate_fields.copy() + (additional_rate_fields or [])
		rate_fields += [('tax_exclusive_' + t, 'tax_exclusive_' + s) for t, s in rate_fields
			if item_meta.has_field('tax_exclusive_' + t) and item_meta.has_field('tax_exclusive_' + s)]

		base_fields = [('base_' + f, f) for f in sum_fields if item_meta.has_field('base_' + f)]
		base_fields += [('base_' + t, t) for t, s in rate_fields if item_meta.has_field('base_' + t)]

		# Sum amounts
		for item in self.items:
			group_key = (cstr(item.item_code), cstr(item.item_name), item.uom)
			group_item = group_item_data.setdefault(group_key, frappe._dict())
			self.merge_similar_item_aggregate(item, group_item, sum_fields)

		# Calculate average rates and get serial nos string
		for group_item in group_item_data.values():
			self.merge_similar_items_postprocess(group_item, rate_fields)

		# Calculate company currenct values
		for group_item in group_item_data.values():
			for target, source in base_fields:
				group_item[target] = group_item.get(source, 0) * self.conversion_rate

		# Remove duplicates and set aggregated values
		duplicate_list = []
		for item in self.items:
			group_key = (cstr(item.item_code), cstr(item.item_name), item.uom)
			if group_key in group_item_data.keys():
				count += 1

				# Will set price_list_rate instead
				if item.get('rate_with_margin'):
					item.rate_with_margin = 0
				if item.get('tax_exclusive_rate_with_margin'):
					item.tax_exclusive_rate_with_margin = 0

				item.update(group_item_data[group_key])

				item.idx = count
				del group_item_data[group_key]
			else:
				duplicate_list.append(item)

		for item in duplicate_list:
			self.remove(item)

	def merge_similar_item_aggregate(self, item, group_item, sum_fields):
		for f in sum_fields:
			group_item[f] = group_item.get(f, 0) + flt(item.get(f))

		group_item_serial_nos = group_item.setdefault('serial_no', [])
		if item.get('serial_no'):
			group_item_serial_nos += filter(lambda s: s, item.serial_no.split('\n'))

		group_item_tax_detail = group_item.setdefault('item_tax_detail', {})
		item_tax_detail = json.loads(item.item_tax_detail or '{}')
		for tax_row_name, tax_amount in item_tax_detail.items():
			group_item_tax_detail.setdefault(tax_row_name, 0)
			group_item_tax_detail[tax_row_name] += flt(tax_amount)

	def merge_similar_items_postprocess(self, group_item, rate_fields):
		if group_item.qty:
			for target, source in rate_fields:
				if target == "price_list_rate" and group_item.get('amount_before_depreciation'):
					source = 'amount_before_depreciation'
				if target == "tax_exclusive_price_list_rate" and group_item.get('tax_exclusive_amount_before_depreciation'):
					source = 'tax_exclusive_amount_before_depreciation'
				group_item[target] = flt(group_item[source]) / flt(group_item.qty)
		else:
			for target, source in rate_fields:
				group_item[target] = 0

		group_item.serial_no = '\n'.join(group_item.serial_no)
		group_item.item_tax_detail = json.dumps(group_item.item_tax_detail)

		group_item.discount_percentage = group_item.total_discount / group_item.amount_before_discount * 100\
			if group_item.amount_before_discount else group_item.discount_percentage

		if self.doctype == "Sales Invoice":
			group_item.depreciation_percentage = group_item.depreciation_amount / group_item.amount_before_depreciation * 100\
				if group_item.amount_before_depreciation else group_item.depreciation_percentage

	def group_items_by_item_tax_and_item_group(self):
		grouped = self.group_items_by(key="item_tax_template")
		for item_tax_template, group_data in grouped.items():
			# group item groups in item tax template group
			group_data.item_groups = self.group_items_by_item_group(group_data['items'])

		# reset item index
		item_idx = 1
		for item_tax_group in grouped.values():
			for item_group_group in item_tax_group.item_groups.values():
				for item in item_group_group['items']:
					item.g_idx = item_idx
					item_idx += 1

		return grouped

	def group_items_by_item_group(self, items):
		grouped = self.group_items_by(key=lambda row: self.get_item_group_print_heading(row), items=items)

		# Sort by Item Group Order
		out = OrderedDict()
		price_list_settings = frappe.get_cached_doc("Price List Settings", None)

		for d in price_list_settings.item_group_order:
			if d.item_group in grouped:
				out[d.item_group] = grouped[d.item_group]
				del grouped[d.item_group]

		for item_group, group_data in grouped.items():
			out[item_group] = grouped[item_group]

		# reset item index
		item_idx = 1
		for item_group_group in grouped.values():
			for item in item_group_group['items']:
				item.ig_idx = item_idx
				item_idx += 1

		return out

	def group_items_by(self, key, items=None):
		grouped = OrderedDict()

		if not items:
			items = self.get("items") or []

		for item in items:
			if callable(key):
				key_value = key(item)
			elif isinstance(key, (tuple, list)):
				key_value = tuple(cstr(item.get(k)) for k in key)
			else:
				key_value = cstr(item.get(key))

			group_data = grouped.setdefault(key_value, frappe._dict({"items": []}))
			group_data['items'].append(item)

		# calculate group totals
		self.group_items_by_postprocess(grouped)
		return grouped

	def group_items_by_postprocess(self, grouped):
		for key_value, group_data in grouped.items():
			group_data.uom = self.get_common_uom(group_data["items"])
			group_data.stock_uom = self.get_common_uom(group_data["items"], "stock_uom")

			for group_field, item_field in print_total_fields_from_items:
				group_data[group_field] = sum([flt(d.get(item_field)) for d in group_data['items']])
				group_data["base_" + group_field] = group_data[group_field] * self.conversion_rate

			self.calculate_taxes_for_group(group_data)

	def get_common_uom(self, items, uom_field="uom"):
		unique_group_uoms = list(set(row.get(uom_field) for row in items if row.get(uom_field)))
		return unique_group_uoms[0] if len(unique_group_uoms) == 1 else ""

	def get_item_group_print_heading(self, item):
		from erpnext.setup.doctype.item_group.item_group import get_item_group_print_heading
		return get_item_group_print_heading(item.item_group)

	def calculate_taxes_for_group(self, group_data, taxes_as_dict=False):
		tax_copy_fields = ['name', 'idx', 'account_head', 'description', 'charge_type', 'row_id']

		# initialize tax rows for item tax template group
		group_data.taxes = OrderedDict()
		for tax in self.taxes:
			new_tax_row = frappe._dict({k:v for (k, v) in tax.as_dict().items() if k in tax_copy_fields})
			new_tax_row.tax_amount_after_discount_amount = 0
			new_tax_row.tax_amount = 0
			new_tax_row.total = 0
			new_tax_row.default_rate = flt(tax.rate)

			group_data.taxes[tax.name] = new_tax_row

		# sum up tax amounts
		for item in group_data['items']:
			item_tax_detail = json.loads(item.item_tax_detail or '{}')
			for tax_row_name, tax_amount in item_tax_detail.items():
				group_data.taxes[tax_row_name].tax_amount_after_discount_amount += flt(tax_amount)

			item_tax_detail_before_discount = json.loads(item.item_tax_detail_before_discount or '{}')
			for tax_row_name, tax_amount in item_tax_detail_before_discount.items():
				group_data.taxes[tax_row_name].tax_amount += flt(tax_amount)

		# calculate total after taxes
		for i, tax in enumerate(group_data.taxes.values()):
			if i == 0:
				tax.total = group_data.taxable_total + tax.tax_amount_after_discount_amount
			else:
				tax.total = list(group_data.taxes.values())[i-1].total + tax.tax_amount_after_discount_amount

		# get default tax rate
		default_tax_rate = {}
		default_tax_item_rates = {}
		for item in group_data['items']:
			item_tax_rate = json.loads(item.item_tax_rate or '{}')
			for tax in group_data.taxes.values():
				rate = tax.default_rate
				if tax.account_head in item_tax_rate:
					rate = flt(item_tax_rate[tax.account_head])

				default_tax_item_rates.setdefault(tax.account_head, []).append(rate)

		for account_head, rate_list in default_tax_item_rates.items():
			if len(set(rate_list)) == 1:
				default_tax_rate[account_head] = rate_list[0]

		# calculate tax rates
		for i, tax in enumerate(group_data.taxes.values()):
			if tax.charge_type in ('On Previous Row Total', 'On Previous Row Amount'):
				fieldname = 'total' if tax.charge_type == 'On Previous Row Total' else 'tax_amount_after_discount_amount'
				prev_row_taxable = list(group_data.taxes.values())[cint(tax.row_id)-1].get(fieldname)
				tax.rate = (tax.tax_amount_after_discount_amount / prev_row_taxable) * 100 if prev_row_taxable else 0
			else:
				tax.rate = (tax.tax_amount_after_discount_amount / group_data.taxable_total) * 100 if group_data.taxable_total\
					else flt(default_tax_rate.get(tax.account_head))

		if not taxes_as_dict:
			group_data.taxes = list(group_data.taxes.values())

	def get_taxes_for_item(self, item):
		group_data = frappe._dict()
		group_data.items = [item]
		group_data.taxable_total = item.taxable_amount
		self.calculate_taxes_for_group(group_data)
		return group_data.taxes

	def calculate_tax_rates_for_print(self):
		for tax in self.taxes:
			self.calculate_tax_rate(tax)

	def calculate_tax_rate(self, tax):
		group_data = frappe._dict({'items': [], 'taxable_total': 0})
		for item in self.items:
			item_tax_detail = json.loads(item.item_tax_detail or '{}')
			if item_tax_detail.get(tax.name):
				group_data['items'].append(item)
				group_data.taxable_total += item.taxable_amount

		self.calculate_taxes_for_group(group_data, taxes_as_dict=True)
		tax.calculated_rate = group_data.taxes[tax.name].rate

		if not tax.rate and tax.charge_type in ('On Net Total', 'On Previous Row Total', 'On Previous Row Amount'):
			tax.rate = tax.calculated_rate

	def get_gl_entries_for_print(self):
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
		dimension_fields = get_accounting_dimensions()

		if self.docstatus == 1:
			gles = frappe.get_all("GL Entry", filters={"voucher_type": self.doctype, "voucher_no": self.name},
				fields=['account', 'remarks', 'party_type', 'party', 'debit', 'credit',
					'against_voucher', 'against_voucher_type', 'reference_no', 'reference_date'] + dimension_fields)
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

		debit_gles = list(filter(lambda d: d.debit - d.credit > 0, grouped_gles.values()))
		credit_gles = list(filter(lambda d: d.debit - d.credit < 0, grouped_gles.values()))

		self.gl_entries = debit_gles + credit_gles
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

	def get_vehicle_details_map(self):
		self.vehicle_details_map = {}

		if 'Vehicles' not in frappe.get_active_domains():
			return

		def add_to_vehicle_details(doc):
			if doc.get('applies_to_vehicle'):
				vehicle_details = frappe._dict()

				if doc.get('applies_to_item_name'):
					vehicle_details.item_name = doc.get('applies_to_item_name')
				if doc.get('vehicle_chassis_no'):
					vehicle_details.chassis_no = doc.get('vehicle_chassis_no')
				if doc.get('vehicle_engine_no'):
					vehicle_details.engine_no = doc.get('vehicle_engine_no')
				if doc.get('vehicle_license_plate'):
					vehicle_details.license_plate = doc.get('vehicle_license_plate')

				self.vehicle_details_map[doc.applies_to_vehicle] = vehicle_details

		if self.doctype == "Journal Entry":
			for d in self.accounts:
				add_to_vehicle_details(d)

		add_to_vehicle_details(self)


	def set_payment_schedule(self):
		if self.doctype == 'Sales Invoice' and self.is_pos:
			self.payment_terms_template = ''
			return

		posting_date = self.get("posting_date") or self.get("transaction_date")
		bill_date = self.get("bill_date") if self.doctype != 'Vehicle Booking Order' else None
		due_date = self.get("due_date") or posting_date
		grand_total = flt(self.get("rounded_total") or self.get('invoice_total') or self.get('grand_total'))
		if self.doctype in ("Sales Invoice", "Purchase Invoice"):
			grand_total = grand_total - flt(self.write_off_amount)

		if self.get("total_advance"):
			grand_total -= self.get("total_advance")

		remaining_amount = grand_total

		if not self.get("payment_schedule"):
			if self.get("payment_terms_template"):
				data = get_payment_terms(self.payment_terms_template, posting_date, grand_total,
					delivery_date=self.get('delivery_date'), bill_date=bill_date)
				for item in data:
					self.append("payment_schedule", item)
			else:
				data = dict(due_date=due_date, invoice_portion=100, payment_amount=grand_total, payment_amount_type="Percentage")
				self.append("payment_schedule", data)
		else:
			for d in self.get("payment_schedule"):
				if d.payment_term:
					term = frappe.get_cached_doc("Payment Term", d.payment_term)
					d.due_date = get_due_date(term, posting_date, bill_date, delivery_date=self.get('delivery_date'))
					if getdate(d.due_date) < getdate(posting_date):
						d.due_date = posting_date
				elif getdate(d.due_date) < getdate(posting_date):
					d.due_date = posting_date

		for d in self.get("payment_schedule"):
			if d.payment_amount_type == "Remaining Amount":
				d.payment_amount = flt(remaining_amount, d.precision('payment_amount'))
			elif d.payment_amount_type == "Amount":
				d.payment_amount = flt(min(d.payment_amount, grand_total), d.precision('payment_amount'))
			else:
				d.payment_amount = flt(grand_total * flt(d.invoice_portion) / 100, d.precision('payment_amount'))

			remaining_amount -= d.payment_amount

			if d.payment_amount_type in ("Amount", "Remaining Amount"):
				d.invoice_portion = flt(d.payment_amount / grand_total * 100) if grand_total else 0

	def set_due_date(self):
		due_dates = [getdate(d.due_date) for d in self.get("payment_schedule") if d.due_date]
		if due_dates:
			self.due_date = max(due_dates)

	def validate_payment_schedule_dates(self):
		dates = []
		li = []

		if self.doctype == 'Sales Invoice' and self.is_pos: return

		for d in self.get("payment_schedule"):
			if self.doctype in ("Sales Order", "Vehicle Booking Order") and getdate(d.due_date) < getdate(self.transaction_date):
				frappe.throw(_("Row {0}: Due Date in the Payment Terms table cannot be before Transaction Date").format(d.idx))
			if d.due_date in dates:
				li.append(_("{0} in row {1}").format(frappe.format(getdate(d.due_date)), d.idx))
			dates.append(d.due_date)

		if li:
			duplicates = '<br>' + '<br>'.join(li)
			frappe.msgprint(_("Payment Schedule rows with duplicate Due Dates found: {0}").format(duplicates),
				alert=1, indicator='orange')

	def validate_payment_schedule_amount(self):
		if self.doctype == 'Sales Invoice' and self.is_pos: return

		total_fieldname = "grand_total"
		rounding_fieldname = "grand_total"
		if self.get("rounded_total"):
			total_fieldname = 'rounded_total'
		elif self.get("invoice_total"):
			total_fieldname = 'invoice_total'
			rounding_fieldname = 'invoice_total'

		if self.get("payment_schedule"):
			total = 0
			for d in self.get("payment_schedule"):
				total += flt(d.payment_amount)
			total = flt(total, self.precision(rounding_fieldname))

			grand_total = flt(self.get(total_fieldname), self.precision(rounding_fieldname))
			if self.get("total_advance"):
				grand_total -= self.get("total_advance")

			if self.doctype in ("Sales Invoice", "Purchase Invoice"):
				grand_total = grand_total - flt(self.write_off_amount)
			if total != flt(grand_total, self.precision(rounding_fieldname)):
				frappe.throw(_("Total Payment Amount in Payment Schedule must be equal to Grand / Rounded Total"))

	def is_rounded_total_disabled(self):
		if self.meta.get_field("calculate_tax_on_company_currency") and cint(self.get("calculate_tax_on_company_currency")) and self.currency != self.company_currency:
			return True
		if self.meta.get_field("disable_rounded_total"):
			return self.disable_rounded_total
		else:
			return frappe.db.get_single_value("Global Defaults", "disable_rounded_total")

	def validate_transaction_type(self):
		if self.get('transaction_type'):
			doc = frappe.get_cached_doc("Transaction Type", self.get('transaction_type'))
			dt_not_allowed = [d.document_type for d in doc.document_types_not_allowed]
			if self.doctype in dt_not_allowed:
				frappe.throw(_("Not allowed to create {0} for Transaction Type {1}")
					.format(frappe.bold(self.doctype), frappe.bold(self.get('transaction_type'))))

			if self.meta.has_field('allocate_advances_automatically') and doc.allocate_advances_automatically:
				self.allocate_advances_automatically = cint(doc.allocate_advances_automatically == "Yes")

			if self.meta.has_field('disable_rounded_total') and doc.disable_rounded_total:
				self.disable_rounded_total = cint(doc.disable_rounded_total == "Yes")

			if self.meta.has_field('calculate_tax_on_company_currency') and doc.calculate_tax_on_company_currency:
				self.calculate_tax_on_company_currency = cint(doc.calculate_tax_on_company_currency == "Yes")

	def validate_zero_outstanding(self):
		if self.get('transaction_type'):
			validate_zero_outstanding = cint(frappe.get_cached_value('Transaction Type', self.get('transaction_type'),
				'validate_zero_outstanding'))

			if validate_zero_outstanding and self.outstanding_amount != 0:
				frappe.throw(_("Outstanding Amount must be 0 for Transaction Type {0}")
					.format(frappe.bold(self.get('transaction_type'))))

	def calculate_sales_team_contribution(self, net_total):
		if not self.meta.get_field("sales_team"):
			return

		net_total = flt(net_total)
		total_allocated_percentage = 0.0
		sales_team = self.get("sales_team") or []

		for sales_person in sales_team:
			self.round_floats_in(sales_person)

			sales_person.allocated_amount = flt(net_total * sales_person.allocated_percentage / 100.0,
				sales_person.precision("allocated_amount"))

			sales_person.incentives = flt(sales_person.allocated_amount * sales_person.commission_rate / 100.0,
				sales_person.precision("incentives"))

			total_allocated_percentage += sales_person.allocated_percentage

		if sales_team and total_allocated_percentage != 100.0:
			throw(_("Total allocated percentage for Sales Team should be 100%"))

	def validate_item_code_mandatory(self):
		for d in self.items:
			if not d.item_code:
				frappe.throw(_("Row #{0}: Item Code is mandatory").format(d.idx))

	def set_project_reference_no(self):
		if self.meta.has_field('project_reference_no'):
			if self.get('project'):
				self.project_reference_no = frappe.db.get_value("Project", self.project, 'reference_no')
			else:
				self.project_reference_no = None


@frappe.whitelist()
def get_tax_rate(account_head):
	return frappe.db.get_value("Account", account_head, ["tax_rate", "account_name", "exclude_from_item_tax_amount"],
		as_dict=True)


@frappe.whitelist()
def get_default_taxes_and_charges(master_doctype, tax_template=None, company=None):
	if not company: return {}

	if tax_template and company:
		tax_template_company = frappe.db.get_value(master_doctype, tax_template, "company")
		if tax_template_company == company:
			return

	default_tax = frappe.db.get_value(master_doctype, {"is_default": 1, "company": company})

	return {
		'taxes_and_charges': default_tax,
		'taxes': get_taxes_and_charges(master_doctype, default_tax)
	}


@frappe.whitelist()
def get_taxes_and_charges(master_doctype, master_name):
	if not master_name:
		return
	from frappe.model import default_fields
	tax_master = frappe.get_doc(master_doctype, master_name)

	taxes_and_charges = []
	for i, tax in enumerate(tax_master.get("taxes")):
		tax = tax.as_dict()

		for fieldname in default_fields:
			if fieldname in tax:
				del tax[fieldname]

		taxes_and_charges.append(tax)

	return taxes_and_charges


def validate_conversion_rate(currency, conversion_rate, conversion_rate_label, company):
	"""common validation for currency and price list currency"""

	company_currency = frappe.get_cached_value('Company',  company,  "default_currency")

	if not conversion_rate:
		throw(_("{0} is mandatory. Maybe Currency Exchange record is not created for {1} to {2}.").format(
			conversion_rate_label, currency, company_currency))


def validate_taxes_and_charges(tax):
	if tax.charge_type in ['Actual', 'On Net Total'] and tax.row_id:
		frappe.throw(_("Can refer row only if the charge type is 'On Previous Row Amount' or 'Previous Row Total'"))
	elif tax.charge_type in ['On Previous Row Amount', 'On Previous Row Total']:
		if cint(tax.idx) == 1:
			frappe.throw(
				_("Cannot select charge type as 'On Previous Row Amount' or 'On Previous Row Total' for first row"))
		elif not tax.row_id:
			frappe.throw(_("Please specify a valid Row ID for row {0} in table {1}".format(tax.idx, _(tax.doctype))))
		elif tax.row_id and cint(tax.row_id) >= cint(tax.idx):
			frappe.throw(_("Cannot refer row number greater than or equal to current row number for this Charge type"))

	if tax.charge_type == "Actual":
		tax.rate = None


def validate_inclusive_tax(tax, doc):
	def _on_previous_row_error(row_range):
		throw(_("To include tax in row {0} in Item rate, taxes in rows {1} must also be included").format(tax.idx,
																										  row_range))

	if cint(getattr(tax, "included_in_print_rate", None)):
		if tax.charge_type == "Actual":
			# inclusive tax cannot be of type Actual
			throw(_("Charge of type 'Actual' in row {0} cannot be included in Item Rate").format(tax.idx))
		elif tax.charge_type == "Weighted Distribution":
			# inclusive tax cannot be of type Actual
			throw(_("Charge of type 'Weighted Distribution' in row {0} cannot be included in Item Rate").format(tax.idx))
		elif tax.charge_type == "On Previous Row Amount" and \
				not cint(doc.get("taxes")[cint(tax.row_id) - 1].included_in_print_rate):
			# referred row should also be inclusive
			_on_previous_row_error(tax.row_id)
		elif tax.charge_type == "On Previous Row Total" and \
				not all([cint(t.included_in_print_rate) for t in doc.get("taxes")[:cint(tax.row_id) - 1]]):
			# all rows about the reffered tax should be inclusive
			_on_previous_row_error("1 - %d" % (tax.row_id,))
		elif tax.get("category") == "Valuation":
			frappe.throw(_("Valuation type charges can not be marked as Inclusive"))


def set_balance_in_account_currency(gl_dict, account_currency=None, conversion_rate=None, company_currency=None):
	if (not conversion_rate) and (account_currency != company_currency):
		frappe.throw(_("Account: {0} with currency: {1} can not be selected")
					 .format(gl_dict.account, account_currency))

	gl_dict["account_currency"] = company_currency if account_currency == company_currency \
		else account_currency

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
				"Journal Entry" as reference_type, je.name as reference_name, je.remark as remarks,
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
				"Payment Entry" as reference_type, pe.name as reference_name,
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
			select "Payment Entry" as reference_type, name as reference_name, remarks, unallocated_amount as amount,
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


def update_invoice_status():
	# Daily update the status of the invoices

	frappe.db.sql(""" update `tabSales Invoice` set status = 'Overdue'
		where due_date < CURDATE() and docstatus = 1 and outstanding_amount > 0""")

	frappe.db.sql(""" update `tabPurchase Invoice` set status = 'Overdue'
		where due_date < CURDATE() and docstatus = 1 and outstanding_amount > 0""")


@frappe.whitelist()
def get_payment_terms(terms_template, posting_date=None, grand_total=None, bill_date=None, delivery_date=None):
	if not terms_template:
		return

	terms_doc = frappe.get_cached_doc("Payment Terms Template", terms_template)

	schedule = []
	remaining_amount = flt(grand_total)
	for d in terms_doc.get("terms"):
		term_details = get_payment_term_details(d, posting_date, grand_total, bill_date, delivery_date=delivery_date,
			remaining_amount=remaining_amount)

		remaining_amount -= term_details.payment_amount
		schedule.append(term_details)

	return schedule


@frappe.whitelist()
def get_payment_term_details(term, posting_date=None, grand_total=None, bill_date=None, delivery_date=None,
		remaining_amount=0):
	term_details = frappe._dict()
	if isinstance(term, text_type):
		term = frappe.get_cached_doc("Payment Term", term)
	else:
		term_details.payment_term = term.payment_term
	term_details.description = term.description
	term_details.invoice_portion = term.invoice_portion

	term_details.payment_amount_type = term.payment_amount_type or "Percentage"
	if term_details.payment_amount_type == "Amount":
		term_details.payment_amount = min(term.payment_amount, flt(grand_total))
	elif term_details.payment_amount_type == "Remaining Amount":
		term_details.payment_amount = flt(remaining_amount)
	else:
		term_details.payment_amount = flt(term.invoice_portion) * flt(grand_total) / 100

	if term_details.payment_amount_type in ("Amount", "Remaining Amount"):
		term_details.invoice_portion = flt(term_details.payment_amount / flt(grand_total) * 100) if flt(grand_total) else 0

	if bill_date:
		term_details.due_date = get_due_date(term, bill_date, delivery_date=delivery_date)
	elif posting_date:
		term_details.due_date = get_due_date(term, posting_date, delivery_date=delivery_date)

	if getdate(term_details.due_date) < getdate(posting_date):
		term_details.due_date = posting_date
	term_details.mode_of_payment = term.mode_of_payment

	return term_details


def get_due_date(term, posting_date=None, bill_date=None, delivery_date=None):
	due_date = None
	date = bill_date or posting_date
	delivery_date = delivery_date or date
	if term.due_date_based_on == "Day(s) after invoice date":
		due_date = add_days(date, term.credit_days)
	elif term.due_date_based_on == "Day(s) after the end of the invoice month":
		due_date = add_days(get_last_day(date), term.credit_days)
	elif term.due_date_based_on == "Month(s) after the end of the invoice month":
		due_date = add_months(get_last_day(date), term.credit_months)
	elif term.due_date_based_on == "Day(s) before delivery date":
		due_date = add_days(delivery_date, term.credit_days * -1)
	return due_date


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
		if parent.is_subcontracted == "Yes":
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
