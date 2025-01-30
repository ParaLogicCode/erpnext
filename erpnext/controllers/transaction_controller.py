import frappe
from frappe import _
from frappe.utils import flt, cstr, cint
from erpnext.controllers.stock_controller import StockController
from erpnext.stock.get_item_details import get_item_details, get_applies_to_details, get_force_applies_to_fields
from erpnext.accounts.doctype.pricing_rule.utils import (
	apply_pricing_rule_for_free_items, get_applied_pricing_rules,
	apply_pricing_rule_on_transaction, update_pricing_rule_table
)
from erpnext.setup.utils import get_exchange_rate
from erpnext.controllers.sales_and_purchase_return import validate_return
from collections import OrderedDict
import json


class TransactionController(StockController):
	selling_or_buying = None

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		self.force_party_fields = [
			"customer_name", "bill_to_name", "supplier_name",
			"customer_group", "supplier_group",
			"contact_display", "contact_mobile", "contact_phone", "contact_email",
			"address_display", "company_address_display",
			"customer_credit_limit", "customer_credit_balance", "customer_outstanding_amount",
			"previous_outstanding_amount",
			"tax_id", "tax_cnic", "tax_strn",
			"retail_price_list",
		]

		self.force_item_fields = [
			"item_group", "brand", "item_source",
			"stock_uom", "alt_uom", "alt_uom_size",
			"item_tax_rate", "pricing_rules", "allow_zero_valuation_rate",
			"is_stock_item", "is_fixed_asset" "has_batch_no", "has_serial_no", "is_vehicle",
			"claim_customer", "force_default_warehouse",
			"sales_commission_category", "commission_rate", "retail_rate",
			"ignore_depreciation",
		]

		self.merge_items_sum_fields = [
			'qty', 'stock_qty', 'alt_uom_qty', 'net_weight',
			'amount', 'taxable_amount', 'net_amount', 'total_discount', 'amount_before_discount',
			'item_taxes', 'item_taxes_before_discount', 'tax_inclusive_amount', 'tax_inclusive_amount_before_discount',
			'amount_before_depreciation', 'depreciation_amount', 'underinsurance_amount',
		]

		self.merge_items_rate_fields = [
			('rate', 'amount', 'qty'),
			('taxable_rate', 'taxable_amount', 'qty'),
			('net_rate', 'net_amount', 'qty'),
			('discount_amount', 'total_discount', 'qty'),
			('price_list_rate', 'amount_before_discount', 'qty'),
			('tax_inclusive_rate', 'tax_inclusive_amount', 'qty'),
			('tax_inclusive_rate_before_discount', 'tax_inclusive_amount_before_discount', 'qty'),
			('net_weight_per_unit', 'net_weight', 'stock_qty'),
			('alt_uom_rate', 'amount', 'stock_qty'),
		]

		self.print_total_fields_from_items = [
			('total_qty', 'qty'),
			('total_alt_uom_qty', 'alt_uom_qty'),
			('total_stock_qty', 'stock_qty'),
			('total_net_weight', 'net_weight'),

			('total', 'amount'),
			('tax_exclusive_total', 'tax_exclusive_amount'),
			('retail_total', 'retail_amount'),
			('net_total', 'net_amount'),
			('taxable_total', 'taxable_amount'),

			('total_discount', 'total_discount'),
			('tax_exclusive_total_discount', 'tax_exclusive_total_discount'),
			('total_before_discount', 'amount_before_discount'),
			('tax_exclusive_total_before_discount', 'tax_exclusive_amount_before_discount'),

			('total_before_depreciation', 'amount_before_depreciation'),
			('tax_exclusive_total_before_depreciation', 'tax_exclusive_amount_before_depreciation'),
			('total_depreciation', 'depreciation_amount'),
			('tax_exclusive_total_depreciation', 'tax_exclusive_depreciation_amount'),
			('total_underinsurance', 'underinsurance_amount'),
			('tax_exclusive_total_underinsurance', 'tax_exclusive_underinsurance_amount'),

			('grand_total', 'tax_inclusive_amount'),
			('grand_total_before_discount', 'tax_inclusive_amount_before_discount'),
			('total_taxes_and_charges', 'item_taxes'),
			('total_taxes_and_charges_before_discount', 'item_taxes_before_discount'),
		]

		self.force_applies_to_fields = get_force_applies_to_fields(self.doctype)

		frappe.utils.call_hook_method("transaction_controller_init", self)

	def onload(self):
		super().onload()
		self.set_onload("enable_dynamic_bundling", self.dynamic_bundling_enabled())

	def before_print(self, print_settings=None):
		super().before_print(print_settings)

		if self.meta.has_field("set_warehouse"):
			self.set_warehouse_name = frappe.get_cached_value("Warehouse", self.set_warehouse, "warehouse_name")

		self.set_previous_document_reference_before_print()
		self.set_common_uom_before_print()
		self.group_items_before_print()
		self.set_discount_negative_before_print()

	def validate(self):
		self.validate_qty_is_not_zero()
		super().validate()

		if self.meta.get_field("currency"):
			self.before_calculate_taxes_and_totals()
			self.calculate_taxes_and_totals()
			self.validate_grand_total()
			validate_return(self)

		self.validate_payment_schedule()
		self.validate_enabled_taxes_and_charges()
		self.validate_tax_account_company()
		self.validate_transaction_type()
		self.validate_item_project_mandatory()

		if self.doctype != 'Material Request':
			apply_pricing_rule_on_transaction(self)
			update_pricing_rule_table(self)

	def set_missing_values(self, for_validate=False):
		super().set_missing_values(for_validate=for_validate)
		self.set_project_reference_no()

	def postprocess_after_mapping(self, reset_taxes=False):
		self.run_method("set_missing_values")

		if reset_taxes:
			self.run_method("reset_taxes_and_charges")
		else:
			self.run_method("set_taxes_and_charges")

		self.run_method("calculate_taxes_and_totals")

	def get_item_details_parent_args(self):
		parent_dict = {}
		for fieldname in self.meta.get_valid_columns():
			parent_dict[fieldname] = self.get(fieldname)

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

		args["is_subcontracted"] = self.get("is_subcontracted")

		return args

	def set_missing_item_details(self, for_validate=False):
		"""set missing item values"""
		from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

		if self.meta.has_field("items"):
			parent_dict = self.get_item_details_parent_args()

			for item in self.get("items"):
				if not item.get("item_code"):
					continue

				args = self.get_item_details_child_args(item, parent_dict)
				ret = get_item_details(args, self, for_validate=True, overwrite_warehouse=False)

				for fieldname, value in ret.items():
					if not item.meta.get_field(fieldname) or value is None:
						continue

					if item.get(fieldname) is None or fieldname in self.force_item_fields:
						item.set(fieldname, value)
					elif fieldname in ['cost_center', 'conversion_factor'] and not item.get(fieldname):
						item.set(fieldname, value)
					elif fieldname == "serial_no":
						# Ensure that serial numbers are matched against Stock UOM
						item_conversion_factor = item.get("conversion_factor") or 1.0
						item_qty = abs(item.get("qty")) * item_conversion_factor
						if item_qty != len(get_serial_nos(item.get('serial_no'))):
							item.set(fieldname, value)

				if ret.get("pricing_rules"):
					self.apply_pricing_rule_on_items(item, ret)

		self.set_missing_applies_to_details()

	def apply_pricing_rule_on_items(self, item, pricing_rule_args):
		if not pricing_rule_args.get("validate_applied_rule"):
			if pricing_rule_args.get("price_or_product_discount") == 'Price':
				item.set("pricing_rules", pricing_rule_args.get("pricing_rules"))
				item.set("discount_percentage", pricing_rule_args.get("discount_percentage"))
				item.set("discount_amount", pricing_rule_args.get("discount_amount"))
				if pricing_rule_args.get("pricing_rule_for") == "Rate":
					item.set("price_list_rate", pricing_rule_args.get("price_list_rate"))

				if item.get("price_list_rate"):
					item.rate = flt(item.price_list_rate * (1.0 - (flt(item.discount_percentage) / 100.0)),
						item.precision("rate"))

					if item.get('discount_amount'):
						item.rate = item.price_list_rate - item.discount_amount

			elif pricing_rule_args.get('free_item_data'):
				apply_pricing_rule_for_free_items(self, pricing_rule_args.get('free_item_data'))

			if item.meta.has_field("margin_type"):
				item.set("margin_type", pricing_rule_args.get("margin_type"))
				item.set("margin_rate_or_amount", pricing_rule_args.get("margin_rate_or_amount"))

		else:
			for pricing_rule in get_applied_pricing_rules(item.get('pricing_rules')):
				pricing_rule_doc = frappe.get_cached_doc("Pricing Rule", pricing_rule)
				for field in ['discount_percentage', 'discount_amount', 'rate']:
					if item.get(field) < pricing_rule_doc.get(field):
						title = frappe.utils.get_link_to_form("Pricing Rule", pricing_rule)

						frappe.msgprint(_("Row {0}: user has not applied the rule {1} on the item {2}")
							.format(item.idx, frappe.bold(title), frappe.bold(item.item_code)))

	def set_missing_applies_to_details(self):
		if not self.meta.has_field('applies_to_item'):
			return

		args = self.get_item_details_parent_args()
		applies_to_details = get_applies_to_details(args, for_validate=True)

		for k, v in applies_to_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in self.force_applies_to_fields:
				self.set(k, v)

	def before_calculate_taxes_and_totals(self):
		pass

	def calculate_taxes_and_totals(self):
		from erpnext.controllers.taxes_and_totals import calculate_taxes_and_totals
		calculate_taxes_and_totals(self)

	def dynamic_bundling_enabled(self):
		return self.doctype in ['Quotation', 'Sales Order', 'Delivery Note', 'Sales Invoice'] and\
			frappe.get_cached_value('Stock Settings', None, "enable_dynamic_bundling")

	def set_previous_document_reference_before_print(self):
		if self.doctype == "Sales Order":
			self.quotations = list(set(item.quotation for item in self.items if item.get("quotation")))
		if self.doctype in ["Sales Invoice", "Delivery Note", "Material Request"]:
			self.sales_orders = list(set(item.sales_order for item in self.items if item.get('sales_order')))
		if self.doctype == "Sales Invoice":
			self.delivery_notes = list(set(item.delivery_note for item in self.items if item.get("delivery_note")))

		if self.doctype == "Purchase Order":
			self.supplier_quotations = list(set(item.supplier_quotation for item in self.items if item.get("supplier_quotation")))
		if self.doctype in ["Purchase Invoice", "Purchase Receipt"]:
			self.purchase_orders = list(set(item.purchase_order for item in self.items if item.get('purchase_order')))
		if self.doctype == "Purchase Invoice":
			self.purchase_receipts = list(set(item.purchase_receipt for item in self.items if item.get("purchase_receipt")))

	def set_common_uom_before_print(self):
		if self.meta.has_field("items"):
			if not self.meta.has_field("uom"):
				self.uom = self.get_common_uom(self.get("items"))
			if not self.meta.has_field("stock_uom"):
				self.stock_uom = self.get_common_uom(self.get("items"), "stock_uom")
			if not self.meta.has_field("weight_uom"):
				self.weight_uom = self.get_common_uom(self.get("items"), "weight_uom")

			for d in self.items:
				d.alt_uom_or_uom = d.get("alt_uom") or d.get("uom")

	def group_items_before_print(self):
		if self.meta.has_field("items"):
			self.original_items = [frappe.copy_doc(d) for d in self.items]

			if self.dynamic_bundling_enabled():
				self.merge_bundled_items()

			if self.get("group_same_items"):
				self.group_similar_items()

			self.items_by_item_group = self.group_items_by_item_group(self.items)
			self.items_by_item_tax_and_item_group = self.group_items_by_item_tax_and_item_group()

			if self.meta.has_field("taxes"):
				self.calculate_tax_rates_for_print()

		self.warehouses = list(set([frappe.get_cached_value("Warehouse", item.warehouse, 'warehouse_name')
			for item in self.items if item.get('warehouse')]))

	def set_discount_negative_before_print(self):
		if self.get("discount_amount"):
			self.discount_amount = -self.discount_amount

		if self.get("total_discount_after_taxes"):
			self.total_discount_after_taxes = -self.total_discount_after_taxes

	def merge_bundled_items(self):
		bundles = {}
		item_meta = frappe.get_meta("Stock Entry Detail" if self.doctype == "Stock Entry" else self.doctype + " Item")
		count = 0

		copy_fields = ['qty', 'stock_qty', 'alt_uom_qty']

		sum_fields = [f for f in self.merge_items_sum_fields if item_meta.has_field(f)]
		sum_fields = [f for f in sum_fields if f not in copy_fields]
		sum_fields += ['tax_exclusive_' + f for f in sum_fields if item_meta.has_field('tax_exclusive_' + f)]

		rate_fields = [(t, s, qf) for t, s, qf in self.merge_items_rate_fields if item_meta.has_field(s)]
		rate_fields += [('tax_exclusive_' + t, 'tax_exclusive_' + s, qf) for t, s, qf in rate_fields
			if item_meta.has_field('tax_exclusive_' + s)]

		base_fields = [('base_' + f, f) for f in sum_fields if item_meta.has_field('base_' + f)]
		base_fields += [('base_' + t, t) for t, s, qf in rate_fields if item_meta.has_field('base_' + t)]
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
		def get_group_key(it):
			if self.get("group_same_items_separate_rate"):
				return cstr(it.item_code), cstr(it.item_name), it.uom, flt(item.rate, item.precision("rate"))
			else:
				return cstr(it.item_code), cstr(it.item_name), it.uom

		group_item_data = {}
		item_meta = frappe.get_meta("Stock Entry Detail" if self.doctype == "Stock Entry" else self.doctype + " Item")
		count = 0

		sum_fields = [f for f in self.merge_items_sum_fields if item_meta.has_field(f)]
		sum_fields += additional_sum_fields or []
		sum_fields += ['tax_exclusive_' + f for f in sum_fields if item_meta.has_field('tax_exclusive_' + f)]

		rate_fields = [(t, s, qf) for t, s, qf in self.merge_items_rate_fields if item_meta.has_field(s)]
		rate_fields += additional_rate_fields or []
		rate_fields += [('tax_exclusive_' + t, 'tax_exclusive_' + s, qf) for t, s, qf in rate_fields
			if item_meta.has_field('tax_exclusive_' + s)]

		base_fields = [('base_' + f, f) for f in sum_fields if item_meta.has_field('base_' + f)]
		base_fields += [('base_' + t, t) for t, s, qf in rate_fields if item_meta.has_field('base_' + t)]

		# Sum amounts
		for item in self.items:
			group_key = get_group_key(item)
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
			group_key = get_group_key(item)
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
		for target, source, qty_field in rate_fields:
			if group_item.get(qty_field):
				if target == "price_list_rate" and group_item.get('amount_before_depreciation'):
					source = 'amount_before_depreciation'
				if target == "tax_exclusive_price_list_rate" and group_item.get('tax_exclusive_amount_before_depreciation'):
					source = 'tax_exclusive_amount_before_depreciation'
				group_item[target] = flt(group_item[source]) / flt(group_item.qty)
			else:
				group_item[target] = 0

		group_item.serial_no = '\n'.join(group_item.serial_no)
		group_item.item_tax_detail = json.dumps(group_item.item_tax_detail)

		group_item.discount_percentage = group_item.total_discount / group_item.amount_before_discount * 100\
			if group_item.amount_before_discount else group_item.discount_percentage

		if self.doctype == "Sales Invoice":
			group_item.depreciation_percentage = group_item.depreciation_amount / group_item.amount_before_depreciation * 100\
				if group_item.amount_before_depreciation else group_item.depreciation_percentage

			amount_after_depreciation = group_item.amount_before_depreciation - group_item.depreciation_amount
			group_item.underinsurance_percentage = group_item.underinsurance_amount / amount_after_depreciation * 100\
				if flt(amount_after_depreciation, 6) else group_item.underinsurance_percentage

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
		for item_group_group in out.values():
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
		item_meta = frappe.get_meta("Stock Entry Detail" if self.doctype == "Stock Entry" else self.doctype + " Item")

		for key_value, group_data in grouped.items():
			group_data.uom = self.get_common_uom(group_data["items"])
			group_data.stock_uom = self.get_common_uom(group_data["items"], "stock_uom")

			for total_field, source_field in self.print_total_fields_from_items:
				if not item_meta.has_field(source_field):
					continue

				group_data[total_field] = sum([flt(d.get(source_field)) for d in group_data['items']])
				if self.meta.has_field("conversion_rate") and self.meta.has_field("base_" + total_field):
					group_data["base_" + total_field] = group_data[total_field] * self.conversion_rate

			if self.meta.has_field("taxes"):
				self.calculate_taxes_for_group(group_data)

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

	def get_common_uom(self, items, uom_field="uom"):
		unique_group_uoms = list(set(row.get(uom_field) for row in items if row.get(uom_field)))
		return unique_group_uoms[0] if len(unique_group_uoms) == 1 else ""

	def get_item_group_print_heading(self, item):
		from erpnext.setup.doctype.item_group.item_group import get_item_group_print_heading
		return get_item_group_print_heading(item.item_group)

	def validate_item_code_mandatory(self):
		for d in self.items:
			if not d.item_code:
				frappe.throw(_("Row #{0}: Item Code is mandatory").format(d.idx))

	def validate_qty_is_not_zero(self):
		if self.get('is_return') and self.doctype in ("Sales Invoice", "Purchase Invoice") and not self.update_stock:
			return

		for item in self.get("items"):
			if not item.qty and not item.get('rejected_qty'):
				frappe.throw(_("Row #{0}: Item Quantity can not be zero").format(item.idx))

	def validate_grand_total(self):
		if not self.get("is_return"):
			self.validate_value("base_grand_total", ">=", 0)
		else:
			self.validate_value("base_grand_total", "<=", 0)

	def set_project_reference_no(self):
		if self.meta.has_field('project_reference_no'):
			if self.get('project'):
				self.project_reference_no = frappe.db.get_value("Project", self.project, 'reference_no')
			else:
				self.project_reference_no = None

	def trigger_project_status(self, action=None):
		projects = []
		if self.get('project'):
			projects.append(self.get('project'))
		for d in self.items:
			if d.get('project'):
				projects.append(d.get('project'))

		projects = list(set(projects))
		for project in projects:
			doc = frappe.get_doc("Project", project)

			doc.validate_project_status_for_transaction(self)
			if self.docstatus == 1:
				doc.validate_for_transaction(self)

			doc.set_status(update=True, from_doctype=self.doctype, action=action or self.get("_action"))
			doc.notify_update()

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

	def validate_item_project_mandatory(self):
		from erpnext.stock.get_item_details import get_project_validation

		if not self.meta.has_field("items") or self.get("project"):
			return

		for d in self.get("items"):
			if not d.meta.has_field("project"):
				return
			if not d.get("item_code") or d.get("project"):
				continue

			selling_or_buying = is_doctype_selling_or_buying(self.doctype)
			project_validation = get_project_validation(d.item_code, self.transaction_type, self.company)

			if (
				project_validation == "Mandatory"
				or (project_validation == "Mandatory for Purchase" and selling_or_buying == "buying")
				or (project_validation == "Mandatory for Sales" and selling_or_buying == "selling")
			):
				frappe.throw(_("Row #{0}: {1} is mandatory for Item {2}").format(
					d.idx, _("Project"), frappe.bold(d.item_code)
				))

	def is_rounded_total_disabled(self):
		if self.meta.get_field("calculate_tax_on_company_currency") and cint(self.get("calculate_tax_on_company_currency")) and self.currency != self.company_currency:
			return True
		if self.meta.get_field("disable_rounded_total"):
			return self.disable_rounded_total
		else:
			return frappe.db.get_single_value("Global Defaults", "disable_rounded_total")

	def update_item_prices(self):
		from erpnext.stock.get_item_details import get_price_list_data, process_args
		from erpnext.stock.report.item_prices.item_prices import _set_item_pl_rate

		parent_dict = frappe._dict({})
		for fieldname in self.meta.get_valid_columns():
			parent_dict[fieldname] = self.get(fieldname)

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
				get_price_list_data(args, frappe.get_cached_doc("Item", item.item_code), item_price_data)

				rate_field_setting = frappe.get_cached_value("Buying Settings", None,
					"update_buying_prices_based_on")
				rate_field = "rate" if rate_field_setting == "Actual Rate" else "price_list_rate"

				new_rate = flt(item.get(rate_field))
				if new_rate and abs(flt(item_price_data.price_list_rate) - new_rate) > 0.005:
					_set_item_pl_rate(args["transaction_date"], item.item_code, self.buying_price_list, new_rate, item.uom, item.conversion_factor)

	def is_inclusive_tax(self):
		is_inclusive = cint(frappe.get_cached_value("Accounts Settings", None, "show_inclusive_tax_in_print"))

		if is_inclusive:
			is_inclusive = 0
			if self.get("taxes", filters={"included_in_print_rate": 1}):
				is_inclusive = 1

		return is_inclusive

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
			if self.company and self.get("customer"):
				# get default tax template from bill to party master otherwise apply tax rule
				from erpnext.accounts.party import set_taxes

				bill_to_party = self.get("bill_to") if self.get("bill_to") else self.customer
				self.taxes_and_charges = set_taxes(
					bill_to_party,
					"Customer",
					posting_date=self.get("transaction_date") or self.get("posting_date"),
					company=self.company,
					customer_group=self.get("customer_group"),
					supplier_group=self.get("supplier_group"),
					tax_category=self.get("tax_category"),
					transaction_type=self.get("transaction_type"),
					cost_center=self.get("cost_center"),
					tax_id=self.get("tax_id"),
					tax_cnic=self.get("tax_cnic"),
					tax_strn=self.get("tax_strn"),
					has_stin=self.get("has_stin"),
					billing_address=self.get("customer_address"),
					shipping_address=self.get("shipping_address_name")
				)

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
		if not self.meta.has_field("taxes_and_charges"):
			return

		taxes_and_charges_doctype = self.meta.get_options("taxes_and_charges")
		if self.taxes_and_charges and frappe.get_cached_value(taxes_and_charges_doctype, self.taxes_and_charges, "disabled"):
			frappe.throw(_("{0} '{1}' is disabled").format(taxes_and_charges_doctype, self.taxes_and_charges))

	def validate_tax_account_company(self):
		if not self.meta.has_field("taxes"):
			return

		for d in self.get("taxes"):
			if d.get("account_head"):
				tax_account_company = frappe.get_cached_value("Account", d.account_head, "company")
				if tax_account_company != self.company:
					frappe.throw(_("Row #{0}: Account {1} does not belong to company {2}").format(
						d.idx, d.account_head, self.company
					))

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

	def set_price_list_currency(self, selling_or_buying):
		if self.meta.get_field("posting_date"):
			transaction_date = self.posting_date
		else:
			transaction_date = self.transaction_date

		if self.meta.get_field("currency"):
			# price list part
			if selling_or_buying.lower() == "selling":
				fieldname = "selling_price_list"
				args = "for_selling"
			else:
				fieldname = "buying_price_list"
				args = "for_buying"

			if self.meta.get_field(fieldname) and self.get(fieldname):
				self.price_list_currency = frappe.db.get_value("Price List", self.get(fieldname), "currency", cache=1)

				if self.price_list_currency == self.company_currency:
					self.plc_conversion_rate = 1.0
				elif not self.plc_conversion_rate:
					self.plc_conversion_rate = get_exchange_rate(self.price_list_currency, self.company_currency, transaction_date, args)

			# currency
			if not self.currency:
				self.currency = self.price_list_currency
				self.conversion_rate = self.plc_conversion_rate
			elif self.currency == self.company_currency:
				self.conversion_rate = 1.0
			elif not self.conversion_rate:
				self.conversion_rate = get_exchange_rate(self.currency, self.company_currency, transaction_date, args)


@frappe.whitelist()
def get_default_taxes_and_charges(master_doctype, tax_template=None, company=None):
	if not company:
		return {}

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
def get_taxes_and_charges(master_doctype, master_name, for_payment_entry=False):
	if not master_name:
		return
	from frappe.model import child_table_fields, default_fields

	tax_master = frappe.get_doc(master_doctype, master_name)

	taxes_and_charges = []
	for _i, tax in enumerate(tax_master.get("taxes")):
		tax = tax.as_dict()

		for fieldname in default_fields + child_table_fields:
			if fieldname in tax:
				del tax[fieldname]

		if cint(for_payment_entry):
			if tax.charge_type == "On Net Total":
				tax.charge_type = "On Paid Amount"
			if not tax.get("add_deduct_tax"):
				tax.add_deduct_tax = "Add"

		taxes_and_charges.append(tax)

	return taxes_and_charges


def validate_taxes_and_charges(tax):
	if tax.charge_type not in ['On Previous Row Amount', 'On Previous Row Total'] and tax.row_id:
		frappe.throw(_("Can refer row only if the charge type is 'On Previous Row Amount' or 'Previous Row Total'"))
	elif tax.charge_type in ('On Previous Row Amount', 'On Previous Row Total'):
		if cint(tax.idx) == 1:
			frappe.throw(_("Cannot select charge type as 'On Previous Row Amount' or 'On Previous Row Total' for first row"))
		elif not tax.row_id:
			frappe.throw(_("Please specify a valid Row ID for row {0} in table {1}".format(tax.idx, _(tax.doctype))))
		elif tax.row_id and cint(tax.row_id) >= cint(tax.idx):
			frappe.throw(_("Cannot refer row number greater than or equal to current row number for this Charge type"))

	if tax.charge_type == "Actual":
		tax.rate = None


def validate_inclusive_tax(tax, doc):
	def _on_previous_row_error(row_range):
		frappe.throw(_("To include tax in row {0} in Item rate, taxes in rows {1} must also be included").format(
			tax.idx, row_range
		))

	if cint(getattr(tax, "included_in_print_rate", None)):
		if tax.charge_type == "Actual":
			# inclusive tax cannot be of type Actual
			frappe.throw(_("Charge of type 'Actual' in row {0} cannot be included in Item Rate").format(tax.idx))
		elif tax.charge_type == "Weighted Distribution":
			# inclusive tax cannot be of type Actual
			frappe.throw(_("Charge of type 'Weighted Distribution' in row {0} cannot be included in Item Rate").format(tax.idx))
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


def is_doctype_selling_or_buying(doctype):
	from frappe.model.document import get_controller

	if not doctype:
		return None

	try:
		controller = get_controller(doctype)
	except Exception:
		return None

	selling_or_buying = getattr(controller, "selling_or_buying", None)
	return selling_or_buying


def set_invoice_as_overdue():
	# Daily update the status of the invoices

	frappe.db.sql(""" update `tabSales Invoice` set status = 'Overdue'
		where due_date < CURDATE() and docstatus = 1 and outstanding_amount > 0""")

	frappe.db.sql(""" update `tabPurchase Invoice` set status = 'Overdue'
		where due_date < CURDATE() and docstatus = 1 and outstanding_amount > 0""")
