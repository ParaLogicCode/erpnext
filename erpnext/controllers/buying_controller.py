# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import flt, cint, cstr, getdate
from frappe.model.utils import get_fetch_values
from erpnext.accounts.party import get_party_details
from erpnext.stock.get_item_details import get_conversion_factor, get_default_supplier, get_default_warehouse
from erpnext.buying.utils import validate_for_items, update_last_purchase_rate
from erpnext.stock.stock_ledger import get_valuation_rate
from erpnext.stock.doctype.stock_entry.stock_entry import get_used_alternative_items
from erpnext.accounts.doctype.budget.budget import validate_expense_against_budget
from erpnext.controllers.transaction_controller import TransactionController
import json


class BuyingController(TransactionController):
	selling_or_buying = "buying"

	def __setup__(self):
		if hasattr(self, "taxes"):
			self.flags.print_taxes_with_zero_amount = cint(frappe.get_cached_value("Print Settings", None,
				 "print_taxes_with_zero_amount"))
			self.flags.show_inclusive_tax_in_print = self.is_inclusive_tax()

			self.print_templates = {
				"total": "templates/print_formats/includes/total.html",
				"taxes": "templates/print_formats/includes/taxes.html"
			}

	def get_feed(self):
		if self.get("supplier_name") or self.get("supplier"):
			return _("From {0} | {1} {2}").format(self.get("supplier_name") or self.get("supplier"), self.currency,
				self.get_formatted("grand_total"))

	def onload(self):
		super(BuyingController, self).onload()

		if self.doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
			self.set_onload("is_internal_supplier",
				frappe.get_cached_value("Supplier", self.supplier, "is_internal_supplier"))

		if self.docstatus == 0:
			if self.get('supplier'):
				self.update(get_fetch_values(self.doctype, 'supplier', self.supplier))

			if self.doctype in ("Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice"):
				self.calculate_taxes_and_totals()

	def validate(self):
		super(BuyingController, self).validate()
		if getattr(self, "supplier", None) and not self.supplier_name:
			self.supplier_name = frappe.db.get_value("Supplier", self.supplier, "supplier_name")

		self.validate_items()
		self.set_qty_as_per_stock_uom()
		self.set_alt_uom_qty()
		self.validate_stock_or_nonstock_items()
		self.validate_warehouse()
		self.validate_asset_return()

		if self.doctype == "Purchase Invoice":
			self.validate_purchase_receipt_if_update_stock()

		if self.doctype == "Purchase Receipt" or (self.doctype == "Purchase Invoice" and self.update_stock):
			# self.validate_purchase_return()
			self.validate_rejected_warehouse()
			self.validate_accepted_rejected_qty()
			validate_for_items(self)

			# subcontracting
			self.validate_for_subcontracting()
			self.set_raw_materials_supplied()
			self.validate_raw_materials_supplied()
			self.set_landed_cost_voucher_amount()

		if self.doctype in ("Purchase Receipt", "Purchase Invoice"):
			self.update_valuation_rate("items")

	def on_submit(self):
		if self.get('is_return'):
			return

		if self.doctype == "Purchase Order":
			if frappe.get_cached_value("Buying Settings", None, "update_buying_prices_on_submission_of_purchase_order"):
				self.update_item_prices()

		if self.doctype in ['Purchase Receipt', 'Purchase Invoice']:
			field = 'purchase_invoice' if self.doctype == 'Purchase Invoice' else 'purchase_receipt'

			self.process_fixed_asset()
			self.update_fixed_asset(field)

		update_last_purchase_rate(self, is_submit=1)

	def on_cancel(self):
		if not self.get('is_return'):
			update_last_purchase_rate(self, is_submit=0)
			if self.doctype in ['Purchase Receipt', 'Purchase Invoice']:
				field = 'purchase_invoice' if self.doctype == 'Purchase Invoice' else 'purchase_receipt'

				self.delete_linked_asset()
				self.update_fixed_asset(field, delete_asset=True)

	def update_status_on_cancel(self):
		to_update = {}
		if self.meta.has_field("status"):
			to_update["status"] = "Cancelled"

		not_applicable_fields = ["billing_status", "receipt_status"]
		for f in not_applicable_fields:
			if self.meta.has_field(f):
				to_update[f] = "Not Applicable"

		if to_update:
			self.db_set(to_update)

	def get_party(self):
		party = self.get("supplier")
		party_name = self.get("supplier_name") if party else None
		return "Supplier", party, party_name

	def get_billing_party(self):
		if self.get("letter_of_credit"):
			return "Letter of Credit", self.get("letter_of_credit"), self.get("letter_of_credit")

		return super().get_billing_party()

	def set_missing_values(self, for_validate=False):
		super(BuyingController, self).set_missing_values(for_validate)

		self.set_default_supplier_warehouse()
		self.set_is_subcontracted()

		self.set_supplier_from_item_default()

		# set contact and address details for supplier, if they are not mentioned
		if self.get("supplier"):
			self.update_if_missing(get_party_details(
				party=self.supplier,
				party_type="Supplier",
				ignore_permissions=self.flags.ignore_permissions,
				letter_of_credit=self.get("letter_of_credit"),
				doctype=self.doctype,
				company=self.company,
				branch=self.get("branch"),
				project=self.get('project'),
				party_address=self.get("supplier_address"),
				shipping_address=self.get('shipping_address'),
				contact_person=self.get('contact_person'),
				account=self.get('credit_to'),
				posting_date=self.get('posting_date') or self.get('transaction_date'),
				bill_date=self.get('bill_date'),
				delivery_date=self.get('schedule_date'),
				currency=self.get('currency'),
				price_list=self.get('buying_price_list'),
				transaction_type=self.get('transaction_type')
			), force_fields=self.force_party_fields)

		self.set_price_list_currency("Buying")
		self.set_missing_item_details(for_validate)

	def set_supplier_from_item_default(self):
		if self.meta.get_field("supplier") and not self.supplier:
			for d in self.get("items"):
				if d.item_code:
					default_supplier = get_default_supplier(d.item_code, self)
					if default_supplier:
						self.supplier = default_supplier
						break

	def validate_stock_or_nonstock_items(self):
		if self.meta.get_field("taxes") and not self.get_stock_items() and not self.get_asset_items():
			tax_for_valuation = [d for d in self.get("taxes") if d.category in ["Valuation", "Valuation and Total"]]

			if tax_for_valuation:
				for d in tax_for_valuation:
					d.category = 'Total'
				frappe.msgprint(_('Tax Category has been changed to "Total" because all the Items are non-stock items'))

	def validate_asset_return(self):
		if self.doctype not in ['Purchase Receipt', 'Purchase Invoice'] or not self.is_return:
			return

		purchase_doc_field = 'purchase_receipt' if self.doctype == 'Purchase Receipt' else 'purchase_invoice'
		not_cancelled_asset = [d.name for d in frappe.db.get_all("Asset", {
			purchase_doc_field: self.return_against,
			"docstatus": 1
		})]
		if self.is_return and len(not_cancelled_asset):
			frappe.throw(_("{} has submitted assets linked to it. You need to cancel the assets to create purchase return.".format(self.return_against)),
				title=_("Not Allowed"))

	def validate_transaction_type(self):
		super(BuyingController, self).validate_transaction_type()

		if self.get('transaction_type'):
			if not frappe.get_cached_value("Transaction Type", self.transaction_type, 'buying'):
				frappe.throw(_("Transaction Type {0} is not allowed for purchase transactions").format(frappe.bold(self.transaction_type)))

	def get_asset_items(self):
		if self.doctype not in ['Purchase Order', 'Purchase Invoice', 'Purchase Receipt']:
			return []

		return [d.item_code for d in self.items if d.is_fixed_asset]

	def set_landed_cost_voucher_amount(self):
		if self.doctype == "Purchase Receipt":
			purchase_item_field = "purchase_receipt_item"
		elif self.doctype == "Purchase Invoice":
			purchase_item_field = "purchase_invoice_item"
		else:
			frappe.throw(_("Can only set Landed Cost Voucher Amount for Purchase Receipt or Purchase Invoice"))

		for d in self.get("items"):
			lc_voucher_data = frappe.db.sql("""
				select sum(applicable_charges)
				from `tabLanded Cost Item`
				where docstatus = 1 and {purchase_item_field} = %s
			""".format(purchase_item_field=purchase_item_field), d.name)

			d.landed_cost_voucher_amount = flt(lc_voucher_data[0][0]) if lc_voucher_data else 0.0

	# update valuation rate
	def update_valuation_rate(self, parentfield):
		"""
			item_tax_amount is the total tax amount applied on that item
			stored for valuation

			TODO: rename item_tax_amount to valuation_tax_amount
		"""
		stock_and_asset_items = self.get_stock_items() + self.get_asset_items()

		stock_and_asset_items_qty, stock_and_asset_items_amount = 0, 0
		last_item_idx = 1
		for d in self.get(parentfield):
			if d.item_code and d.item_code in stock_and_asset_items:
				stock_and_asset_items_qty += flt(d.qty)
				stock_and_asset_items_amount += flt(d.base_net_amount)
				last_item_idx = d.idx

		valuation_taxes = [d for d in self.get("taxes") if d.category in ["Valuation", "Valuation and Total"]]
		total_valuation_amount = sum([flt(d.base_tax_amount_after_discount_amount) for d in valuation_taxes])

		total_non_stock_asset_valuation_tax = 0
		for item in self.get(parentfield):
			if item.qty and item.item_code not in stock_and_asset_items:
				item_tax_detail = json.loads(item.item_tax_detail or '{}')
				for tax in valuation_taxes:
					tax_amount = flt(item_tax_detail.get(tax.name)) * self.conversion_rate
					total_non_stock_asset_valuation_tax += tax_amount

		valuation_amount_adjustment = total_valuation_amount
		for i, item in enumerate(self.get(parentfield)):
			item.valuation_rate = 0.0

			if item.item_code and item.qty and item.item_code in stock_and_asset_items:
				item_proportion = flt(item.base_net_amount) / stock_and_asset_items_amount if stock_and_asset_items_amount \
					else flt(item.qty) / stock_and_asset_items_qty

				item.item_tax_amount = total_non_stock_asset_valuation_tax * item_proportion
				item_tax_detail = json.loads(item.item_tax_detail or '{}')
				for tax in valuation_taxes:
					tax_amount = flt(item_tax_detail.get(tax.name)) * self.conversion_rate
					item.item_tax_amount += tax_amount

				if i == (last_item_idx - 1):
					item.item_tax_amount = flt(valuation_amount_adjustment, self.precision("item_tax_amount", item))
				else:
					item.item_tax_amount = flt(item.item_tax_amount, self.precision("item_tax_amount", item))
					valuation_amount_adjustment -= item.item_tax_amount

				if flt(item.conversion_factor)==0.0:
					item.conversion_factor = get_conversion_factor(item.item_code, item.uom).get("conversion_factor") or 1.0

				qty_in_stock_uom = flt(item.qty * item.conversion_factor)
				rm_supp_cost = flt(item.rm_supp_cost) if self.doctype in ["Purchase Receipt", "Purchase Invoice"] else 0.0

				landed_cost_voucher_amount = flt(item.landed_cost_voucher_amount) \
					if self.doctype in ["Purchase Receipt", "Purchase Invoice"] else 0.0

				valuation_item_tax_amount = self.get_item_valuation_tax_amount(item)
				valuation_net_amount = self.get_item_valuation_net_amount(item)

				item.valuation_rate = ((valuation_net_amount + valuation_item_tax_amount + rm_supp_cost
					+ landed_cost_voucher_amount) / qty_in_stock_uom)

	def get_item_valuation_tax_amount(self, item):
		amt = item.item_tax_amount
		if self.doctype == "Purchase Receipt":
			# If item has been billed/overbilled, use the amount from invoices
			# If item is partially billed, then use the amounts from both with the ratio billed_qty:unbilled_qty
			if item.billed_qty:
				unbilled_qty = max(0, item.qty - item.billed_qty)
				amt = item.billed_item_tax_amount
				amt += item.item_tax_amount * unbilled_qty / item.qty
		return flt(amt, self.precision("item_tax_amount", "items"))

	def get_item_valuation_net_amount(self, item):
		amt = item.base_net_amount
		if self.doctype == "Purchase Receipt":
			# If item has been billed/overbilled, use the amount from invoices
			# If item is partially billed, then use the amounts from both with the ratio billed_qty:unbilled_qty
			if item.billed_qty:
				unbilled_qty = max(0, item.qty - item.billed_qty)
				amt = item.billed_net_amount
				amt += item.base_net_amount * unbilled_qty / item.qty
		elif self.doctype == "Purchase Invoice":
			amt -= flt(item.debit_note_amount)

		return flt(amt, self.precision("base_net_amount", "items"))

	def set_default_supplier_warehouse(self):
		if self.get("is_subcontracted") and not self.get("supplier_warehouse") and self.meta.has_field("supplier_warehouse"):
			self.supplier_warehouse = frappe.get_cached_value("Buying Settings", None,
				"default_subcontracting_supplier_warehouse")

	def set_is_subcontracted(self):
		if self.get("is_return"):
			return

		if any(d for d in self.get("items") if d.get("work_order")):
			self.is_subcontracted = 1

		if self.doctype in ("Purchase Receipt", "Purchase Invoice"):
			purchase_orders = set([d.get("purchase_order") for d in self.items if d.get("purchase_order")])
			if purchase_orders and not self.get("is_subcontracted"):
				if frappe.db.exists("Purchase Order", {"name": ["in", purchase_orders], "is_subcontracted": 1}):
					self.is_subcontracted = 1

			purchase_receipts = set([d.get("purchase_receipt") for d in self.items if d.get("purchase_receipt")])
			if purchase_receipts and not self.get("is_subcontracted"):
				if self.subcontracted_items and frappe.db.exists("Purchase Receipt", {"name": ["in", purchase_receipts], "is_subcontracted": 1}):
					self.is_subcontracted = 1

		if self.doctype == "Purchase Invoice" and not self.update_stock:
			self.is_subcontracted = 0

	def validate_for_subcontracting(self):
		if self.get("is_subcontracted"):
			if not self.supplier_warehouse:
				if self.doctype in ("Purchase Receipt", "Purchase Invoice"):
					frappe.throw(_("Supplier Warehouse is mandatory for subcontracted receipt"))
				elif self.doctype == "Purchase Order" and self.docstatus == 1:
					frappe.throw(_("Supplier Warehouse is mandatory for subcontracted order before submission"))

			for item in self.get("items"):
				if item in self.subcontracted_items and not item.bom:
					frappe.throw(_("Row #{0}: Please select BOM for subcontracted Item {0}").format(
						item.idx, frappe.bold(item.item_code)
					))

		else:
			for item in self.get("items"):
				if item.bom:
					item.bom = None

	def set_raw_materials_supplied(self):
		if self.get("is_subcontracted"):
			backflush_raw_materials_based_on = frappe.get_cached_value("Buying Settings", None,
				"backflush_raw_materials_of_subcontract_based_on")

			if (
				self.doctype in ('Purchase Receipt', 'Purchase Invoice')
				and backflush_raw_materials_based_on != 'BOM'
				and any(d for d in self.get("items") if d.get("purchase_order"))
			):
				self.update_raw_materials_supplied_based_on_transfer()
			else:
				for item in self.get("items"):
					if self.doctype in ["Purchase Receipt", "Purchase Invoice"]:
						item.rm_supp_cost = 0.0

					if item.bom and item.item_code in self.subcontracted_items:
						self.update_raw_materials_supplied_based_on_bom(item)

				self.cleanup_raw_materials_supplied()

		elif self.doctype in ["Purchase Receipt", "Purchase Invoice"]:
			for item in self.get("items"):
				item.rm_supp_cost = 0.0

		if not self.get("is_subcontracted") and self.get("supplied_items"):
			self.set('supplied_items', [])

	def update_raw_materials_supplied_based_on_transfer(self):
		self.set('supplied_items', [])

		for fg_row in self.get('items'):
			# reset raw_material cost
			fg_row.rm_supp_cost = 0

			# get supplied and backflushed data
			transferred_materials_data, to_receive_qty = self.get_raw_materials_supplied_against_item(fg_row)
			backflushed_materials_data = self.get_raw_materials_backflushed_against_item(fg_row)

			# calculate unconsumed / pending qty
			pending_materials = {}
			for d in transferred_materials_data:
				d.pending_qty = d.transferred_qty

				pending_item_dict = pending_materials.setdefault(d.rm_item_code, frappe._dict({
					"total_transferred_qty": 0, "total_pending_qty": 0, "batch_map": {}
				}))

				pending_item_dict.total_transferred_qty += d.transferred_qty
				pending_item_dict.total_pending_qty += d.pending_qty

				pending_item_dict.batch_map.setdefault(d.batch_no, d)

			for d in backflushed_materials_data:
				pending_item_dict = pending_materials.get(d.rm_item_code)
				if pending_item_dict:
					pending_item_dict.total_pending_qty -= d.qty

					pending_ib = pending_item_dict.batch_map.get(d.batch_no)
					if pending_ib:
						pending_ib.pending_qty -= d.qty

			# calculate to consume qty
			qty_precision = 6

			for pending_item_dict in pending_materials.values():
				total_pending_qty = flt(pending_item_dict.total_pending_qty, qty_precision)
				if total_pending_qty <= 0:
					continue

				if flt(to_receive_qty, qty_precision) > 0:
					total_required_qty = pending_item_dict.total_pending_qty * (fg_row.qty / to_receive_qty)
				else:
					total_required_qty = total_pending_qty

				if total_required_qty > pending_item_dict.total_pending_qty:
					total_required_qty = pending_item_dict.total_pending_qty

				total_required_qty = flt(total_required_qty, qty_precision)

				total_to_consume = min(total_required_qty, total_pending_qty)
				total_remaining = total_to_consume

				for pending_ib in pending_item_dict.batch_map.values():
					if flt(total_remaining, qty_precision) <= 0:
						break

					pending_qty = flt(pending_ib.pending_qty, qty_precision)
					if pending_qty <= 0:
						continue

					consumed_qty = min(pending_qty, total_remaining)

					self.append_raw_material_to_be_backflushed(fg_row, pending_ib, consumed_qty)

					total_remaining -= consumed_qty
					pending_ib.pending_qty -= consumed_qty

	def get_raw_materials_supplied_against_item(self, row):
		if not row.get("purchase_order") or not row.get("purchase_order_item") or not row.get("item_code"):
			return [], 0

		transferred_materials_map = self.get_raw_materials_supplied_against_purchase_order(row.purchase_order)
		transferred_materials_data = transferred_materials_map.get(row.item_code) or []

		ordered_lines = frappe.get_all("Purchase Order Item",
			filters={"item_code": row.item_code, "parent": row.purchase_order},
			fields=["name", "qty", "received_qty"]
		)
		po_item = [d for d in ordered_lines if d.name == row.purchase_order_item][0]

		if len(ordered_lines) > 1:
			total_po_qty = sum(d.qty for d in ordered_lines)
			ratio = po_item.qty / total_po_qty
		else:
			ratio = 1

		for d in transferred_materials_data:
			d.transferred_qty *= ratio

		to_receive_qty = flt(po_item.qty) - flt(po_item.received_qty)
		return transferred_materials_data, to_receive_qty

	def get_raw_materials_supplied_against_purchase_order(self, purchase_order):
		from copy import deepcopy

		if self.get("_raw_materials_supplied_against_purchase_order", default={}).get(purchase_order) is None:
			transferred_materials_data = frappe.db.sql("""
				select i.subcontracted_item as main_item_code,
					i.item_code as rm_item_code, ifnull(i.batch_no, '') as batch_no,
					sum(i.stock_qty) as transferred_qty,
					i.item_name as rm_item_name, i.original_item, i.description,
					i.stock_uom, i.conversion_factor
				from `tabStock Entry Detail` i
				inner join `tabStock Entry` ste on ste.name = i.parent
				where ste.docstatus = 1
					and ste.purchase_order = %s
					and ste.purpose = 'Send to Subcontractor'
					and ifnull(i.t_warehouse, '') != ''
				group by main_item_code, rm_item_code, ifnull(i.batch_no, '')
			""", purchase_order, as_dict=1)

			transferred_materials_map = {}
			for d in transferred_materials_data:
				transferred_materials_map.setdefault(d.main_item_code, []).append(d)

			if not self.get("_raw_materials_supplied_against_purchase_order"):
				self._raw_materials_supplied_against_purchase_order = {}

			self._raw_materials_supplied_against_purchase_order[purchase_order] = transferred_materials_map

		return deepcopy(self._raw_materials_supplied_against_purchase_order.get(purchase_order))

	def get_raw_materials_backflushed_against_item(self, row):
		if not row.get("purchase_order") or not row.get("purchase_order_item") or not row.get("item_code"):
			return []

		purchase_receipt_items = frappe.db.sql("""
			select i.parent, i.name
			from `tabPurchase Receipt Item` i
			where i.purchase_order_item = %s and i.docstatus = 1 and i.parent != %s
		""", (row.purchase_order_item, self.name))

		purchase_invoice_items = frappe.db.sql("""
			select i.parent, i.name
			from `tabPurchase Invoice Item` i
			inner join `tabPurchase Invoice` p on p.name = i.parent
			where purchase_order_item = %s and p.docstatus = 1 and p.update_stock = 1 and p.name != %s
		""", (row.purchase_order_item, self.name))

		supplied_item_references = purchase_receipt_items + purchase_invoice_items

		backflushed_materials_data = []
		if supplied_item_references:
			backflushed_materials_data = frappe.db.sql("""
				select main_item_code, rm_item_code, batch_no, sum(consumed_qty) as qty
				from `tabPurchase Receipt Item Supplied` sup
				where sup.docstatus = 1 and (parent, reference_name) in %s
				group by main_item_code, rm_item_code, batch_no
			""", [supplied_item_references], as_dict=1)

		return backflushed_materials_data

	def append_raw_material_to_be_backflushed(self, fg_item_doc, raw_material_data, qty):
		rm = self.append('supplied_items', {})
		rm.update(raw_material_data)

		if not rm.main_item_code:
			rm.main_item_code = fg_item_doc.item_code
		rm.main_item_name = fg_item_doc.item_name

		rm.reference_name = fg_item_doc.name
		rm.required_qty = qty
		rm.consumed_qty = qty

		if not raw_material_data.get('non_stock_item'):
			from erpnext.stock.utils import get_incoming_rate
			rm.rate = get_incoming_rate({
				"item_code": raw_material_data.rm_item_code,
				"warehouse": self.supplier_warehouse,
				"batch_no": rm.batch_no,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"qty": -1 * qty,
				"serial_no": rm.serial_no
			})

			if not rm.rate:
				rm.rate = get_valuation_rate(raw_material_data.rm_item_code, self.supplier_warehouse,
					self.doctype, self.name, rm.batch_no, currency=self.company_currency, company=self.company)

		rm.amount = qty * flt(rm.rate)
		fg_item_doc.rm_supp_cost += rm.amount

	def update_raw_materials_supplied_based_on_bom(self, item):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict

		explode_items = 1
		if item.meta.has_field('include_exploded_items'):
			explode_items = item.get('include_exploded_items')

		received_qty = flt(item.stock_qty) + flt(item.get('rejected_qty')) * flt(item.conversion_factor)
		bom_items = get_bom_items_as_dict(item.bom, self.company, qty=received_qty, fetch_exploded=explode_items,
			fetch_qty_in_stock_uom=True)

		used_alternative_items = []
		if self.doctype == 'Purchase Receipt' and item.purchase_order:
			used_alternative_items = get_used_alternative_items(purchase_order=item.purchase_order)

		raw_materials_cost = 0

		for bom_item in bom_items.values():
			if bom_item.skip_transfer_for_manufacture:
				continue

			reserve_warehouse = None
			if self.doctype == "Purchase Order":
				if item.get("work_order"):
					reserve_warehouse = frappe.db.get_value("Work Order", item.work_order, "source_warehouse", cache=1)

				if not reserve_warehouse:
					reserve_warehouse = bom_item.source_warehouse or get_default_warehouse(bom_item.item_code, self)

				if reserve_warehouse and frappe.get_cached_value("Warehouse", reserve_warehouse, "company") != self.company:
					reserve_warehouse = None

			conversion_factor = item.conversion_factor
			if self.doctype == 'Purchase Receipt' and item.purchase_order and bom_item.item_code in used_alternative_items:
				alternative_item_data = used_alternative_items.get(bom_item.item_code)
				bom_item.item_code = alternative_item_data.item_code
				bom_item.item_name = alternative_item_data.item_name
				bom_item.stock_uom = alternative_item_data.stock_uom
				conversion_factor = alternative_item_data.conversion_factor
				bom_item.description = alternative_item_data.description

			# check if exists
			rm = None
			for d in self.get("supplied_items"):
				if d.main_item_code == item.item_code and d.rm_item_code == bom_item.item_code and d.reference_name == item.name:
					rm = d
					break

			if not rm:
				rm = self.append("supplied_items")

			rm.reference_name = item.name

			rm.main_item_code = item.item_code
			rm.main_item_name = item.item_name

			rm.rm_item_code = bom_item.item_code
			rm.rm_item_name = bom_item.item_name

			rm.stock_uom = bom_item.stock_uom
			rm.required_qty = bom_item.qty
			if self.doctype == "Purchase Order" and not rm.reserve_warehouse:
				rm.reserve_warehouse = reserve_warehouse

			rm.conversion_factor = conversion_factor

			if self.doctype in ("Purchase Receipt", "Purchase Invoice"):
				rm.consumed_qty = rm.required_qty
				if item.batch_no and frappe.db.get_value("Item", rm.rm_item_code, "has_batch_no") and not rm.batch_no:
					rm.batch_no = item.batch_no

			# get raw materials rate
			if self.doctype in ("Purchase Receipt", "Purchase Invoice"):
				from erpnext.stock.utils import get_incoming_rate
				rm.rate = get_incoming_rate({
					"item_code": bom_item.item_code,
					"warehouse": self.supplier_warehouse,
					"batch_no": rm.batch_no,
					"posting_date": self.posting_date,
					"posting_time": self.posting_time,
					"qty": -1 * rm.required_qty,
					"serial_no": rm.serial_no
				})
				if not rm.rate:
					rm.rate = get_valuation_rate(bom_item.item_code, self.supplier_warehouse,
						self.doctype, self.name, rm.batch_no, currency=self.company_currency, company=self.company)
			else:
				rm.rate = bom_item.rate

			rm.amount = rm.required_qty * flt(rm.rate)
			raw_materials_cost += flt(rm.amount)

		if self.doctype in ("Purchase Receipt", "Purchase Invoice"):
			item.rm_supp_cost = raw_materials_cost

	def cleanup_raw_materials_supplied(self):
		parent_items = set()
		for d in self.get("items"):
			if d.item_code in self.subcontracted_items:
				parent_items.add((d.item_code, d.name))

		"""Remove all those child items which are no longer present in main item table"""
		to_remove = []
		for d in self.get("supplied_items"):
			if (d.main_item_code, d.reference_name) not in parent_items:
				to_remove.append(d)

		for d in to_remove:
			self.remove(d)

		for i, d in enumerate(self.get("supplied_items")):
			d.idx = i + 1

	def validate_raw_materials_supplied(self):
		if not self.is_subcontracted:
			return

		for main in self.get("items"):
			if main.item_code in self.subcontracted_items:
				has_supplied_items = any(rm for rm in self.get("supplied_items") if rm.reference_name == main.name)
				if not has_supplied_items:
					frappe.throw(_("Row #{0}: Item {1} does not have any unconsumed supplied materials").format(
						main.idx, frappe.bold(main.item_code)
					))

		for rm in self.get("supplied_items"):
			has_valid_reference = any(main for main in self.get("items") if main.name == rm.reference_name)
			if not has_valid_reference:
				frappe.throw(_("Row #{0}: Raw Material Supplied Item {1} has an invalid reference to subcontracted item").format(
					rm.idx, frappe.bold(main.item_code)
				))

	@property
	def subcontracted_items(self):
		if not hasattr(self, "_subcontracted_items"):
			self._subcontracted_items = []
			item_codes = list(set(item.item_code for item in self.get("items")))
			if item_codes:
				items = frappe.get_all('Item', filters={
					'name': ['in', item_codes],
					'is_sub_contracted_item': 1
				})
				self._subcontracted_items = [item.name for item in items]

		return self._subcontracted_items

	def make_supplied_items_gl_entry(self, gl_entries, sle_map, warehouse_account):
		warehouse_with_no_account = []

		for rm in self.get("supplied_items"):
			stock_value_diff = flt(sle_map.get((rm.name, rm.rm_item_code)))
			if not stock_value_diff:
				continue

			if not warehouse_account.get(self.supplier_warehouse):
				if self.supplier_warehouse not in warehouse_with_no_account:
					warehouse_with_no_account.append(self.supplier_warehouse)
				continue

			main = self.getone("items", {"name": rm.reference_name})
			if not main:
				continue

			gl_entries.append(self.get_gl_dict({
				"account": warehouse_account[self.supplier_warehouse]["account"],
				"against": warehouse_account[main.warehouse]["account"],
				"cost_center": main.cost_center or self.get("cost_center"),
				"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
				"credit": -1 * stock_value_diff
			}, warehouse_account[self.supplier_warehouse]["account_currency"], item=main))

			if not main.get("rm_supp_stock_value_diff"):
				main.rm_supp_stock_value_diff = 0

			main.rm_supp_stock_value_diff += -1 * stock_value_diff

		self.validate_warehouse_with_no_account(warehouse_with_no_account)

	def validate_work_orders(self):
		work_orders = set([d.get("work_order") for d in self.get("items") if d.get("work_order")])
		if not work_orders:
			return

		work_order_details = {}
		for name in work_orders:
			wo = frappe.db.get_value("Work Order", name, [
				"name", "company", "docstatus", "status", "production_item", "fg_warehouse"
			], as_dict=1)
			if not wo:
				frappe.throw(_("Work Order {0} does not exist").format(name))

			work_order_details[name] = wo

		for d in self.get("items"):
			if not d.get("work_order"):
				continue

			wo = work_order_details[d.work_order]

			if wo.docstatus != 1:
				frappe.throw(_("Row #{0}: {1} is not submitted").format(
					d.idx, frappe.get_desk_link("Work Order", wo.name)
				))

			if self.doctype == "Purchase Order" and wo.status == "Stopped":
				frappe.throw(_("Row #{0}: {1} is {2}").format(
					d.idx, frappe.get_desk_link("Work Order", wo.name)), wo.status
				)

			if self.company != wo.company:
				frappe.throw(_("Row #{0}: Company does not match in {1}. Company should be {2}").format(
					d.idx, frappe.get_desk_link("Work Order", wo.name), frappe.bold(wo.company)
				))

			if d.item_code != wo.production_item:
				frappe.throw(_("Row #{0}: Production Item does not match in {1}. Item Code should be {2}").format(
					d.idx, frappe.get_desk_link("Work Order", wo.name), frappe.bold(wo.production_item)
				))

			if self.doctype == "Purchase Order" and d.warehouse != wo.fg_warehouse:
				frappe.throw(_("Row #{0}: Finished Goods Warehouse does not match in {1}. Warehouse should be {2}").format(
					d.idx, frappe.get_desk_link("Work Order", wo.name), frappe.bold(wo.fg_warehouse)
				))

	def get_stock_value_difference_map(self):
		sle_data = frappe.db.sql("""
			select voucher_detail_no, item_code, sum(stock_value_difference) as stock_value_difference
			from `tabStock Ledger Entry`
			where voucher_type = %s and voucher_no = %s
			group by voucher_detail_no, item_code
		""", (self.doctype, self.name), as_dict=1)

		sle_map = {}
		for d in sle_data:
			sle_map[(d.voucher_detail_no, d.item_code)] = d.stock_value_difference

		return sle_map

	def set_qty_as_per_stock_uom(self):
		for d in self.get("items"):
			if d.meta.get_field("stock_qty"):
				# Check if item code is present
				# Conversion factor should not be mandatory for non itemized items
				if not d.conversion_factor and d.item_code:
					frappe.throw(_("Row {0}: Conversion Factor is mandatory").format(d.idx))
				d.stock_qty = flt(flt(d.qty) * flt(d.conversion_factor), 6)

	def set_alt_uom_qty(self):
		for d in self.get("items"):
			if d.meta.get_field("alt_uom_qty"):
				if not d.alt_uom:
					d.alt_uom_size = 1.0
				d.alt_uom_qty = flt(flt(d.stock_qty) * flt(d.alt_uom_size), d.precision("alt_uom_qty"))

	def validate_purchase_return(self):
		for d in self.get("items"):
			if self.is_return and flt(d.rejected_qty) != 0:
				frappe.throw(_("Row #{0}: Rejected Qty can not be entered in Purchase Return").format(d.idx))

			# validate rate with ref PR

	def validate_rejected_warehouse(self):
		for d in self.get("items"):
			if flt(d.rejected_qty) and not d.rejected_warehouse:
				if self.rejected_warehouse:
					d.rejected_warehouse = self.rejected_warehouse

				if not d.rejected_warehouse:
					frappe.throw(_("Row #{0}: Rejected Warehouse is mandatory against rejected Item {1}").format(d.idx, d.item_code))

	# validate accepted and rejected qty
	def validate_accepted_rejected_qty(self):
		for d in self.get("items"):
			self.validate_negative_quantity(d, ["received_qty", "qty", "rejected_qty"])
			if not flt(d.received_qty) and flt(d.qty):
				d.received_qty = flt(d.qty) - flt(d.rejected_qty)

			elif not flt(d.qty) and flt(d.rejected_qty):
				d.qty = flt(d.received_qty) - flt(d.rejected_qty)

			elif not flt(d.rejected_qty):
				d.rejected_qty = flt(d.received_qty) - flt(d.qty)

			val = flt(d.qty) + flt(d.rejected_qty)
			# Check Received Qty = Accepted Qty + Rejected Qty
			if flt(val, d.precision("received_qty")) != flt(d.received_qty, d.precision("received_qty")):
				frappe.throw(_("Accepted + Rejected Qty must be equal to Received quantity for Item {0}").format(d.item_code))

	def validate_negative_quantity(self, item_row, field_list):
		if self.is_return:
			return

		item_row = item_row.as_dict()
		for fieldname in field_list:
			if flt(item_row[fieldname]) < 0:
				frappe.throw(_("Row #{0}: {1} can not be negative for item {2}".format(item_row['idx'],
					frappe.get_meta(item_row.doctype).get_label(fieldname), item_row['item_code'])))

	def check_for_on_hold_or_closed_status(self, ref_doctype, ref_fieldname):
		for d in self.get("items"):
			if d.get(ref_fieldname):
				status = frappe.db.get_value(ref_doctype, d.get(ref_fieldname), "status", cache=1)
				if status in ("Closed", "On Hold"):
					frappe.throw(_("{0} {1} is {2}").format(ref_doctype, d.get(ref_fieldname), status))

	def update_stock_ledger(self, allow_negative_stock=False, via_landed_cost_voucher=False):
		if not frappe.flags.do_not_update_reserved_qty:
			self.update_ordered_and_reserved_qty()

		sl_entries = []

		self.make_sl_entries_for_supplied_items(sl_entries)
		self.make_sl_entries_for_stock_items(sl_entries)

		self.make_sl_entries(sl_entries, allow_negative_stock=allow_negative_stock,
			via_landed_cost_voucher=via_landed_cost_voucher)

	def make_sl_entries_for_stock_items(self, sl_entries):
		stock_items = self.get_stock_items()

		for d in self.get('items'):
			if d.item_code not in stock_items or not d.warehouse:
				continue

			accepted_qty = flt(d.qty) * flt(d.conversion_factor)
			rejected_qty = flt(d.rejected_qty) * flt(d.conversion_factor)

			if accepted_qty:
				accepted_sle = self.get_sl_entries(d, {
					"actual_qty": flt(accepted_qty),
					"serial_no": cstr(d.serial_no).strip()
				})

				# Purchase return outgoing rate
				if self.is_return:
					accepted_sle.update({
						"outgoing_rate": flt(d.valuation_rate, 9)
					})

					# Purchase return dependency
					if self.docstatus == 1:
						purchase_receipt = self.return_against if self.doctype == "Purchase Receipt" else d.get('purchase_receipt')
						if d.get('purchase_receipt_item') and purchase_receipt:
							accepted_sle.dependencies = [{
								"dependent_voucher_type": "Purchase Receipt",
								"dependent_voucher_no": purchase_receipt,
								"dependent_voucher_detail_no": d.purchase_receipt_item,
								"dependency_type": "Rate"
							}]
						elif (
							self.doctype == "Purchase Invoice"
							and d.get('purchase_invoice_item')
							and self.get('return_against')
							and frappe.db.get_value("Purchase Invoice", self.return_against, 'update_stock', cache=1)
						):
							accepted_sle.dependencies = [{
								"dependent_voucher_type": "Purchase Invoice",
								"dependent_voucher_no": self.return_against,
								"dependent_voucher_detail_no": d.purchase_invoice_item,
								"dependency_type": "Rate"
							}]

				# Purchase receipt incoming rate
				else:
					accepted_sle.update({
						"incoming_rate": flt(d.valuation_rate, 9)
					})

					# supplied raw materials dependency
					if self.docstatus == 1:
						for rm in self.get("supplied_items"):
							if rm.reference_name == d.name:
								accepted_sle.setdefault("dependencies", []).append({
									"dependent_voucher_type": self.doctype,
									"dependent_voucher_no": self.name,
									"dependent_voucher_detail_no": rm.name,
									"dependency_type": "Amount",
								})

						if accepted_sle.get("dependencies"):
							incoming_value = flt(d.valuation_rate, 9) * accepted_qty
							accepted_sle.additional_cost = incoming_value - d.rm_supp_cost

				sl_entries.append(accepted_sle)

			if rejected_qty:
				rejected_sle = self.get_sl_entries(d, {
					"warehouse": d.rejected_warehouse,
					"actual_qty": rejected_qty,
					"serial_no": cstr(d.rejected_serial_no).strip(),
					"incoming_rate": 0.0
				})
				if self.is_return:
					rejected_sle.update({
						"outgoing_rate": 0.0
					})

				sl_entries.append(rejected_sle)

	def make_sl_entries_for_supplied_items(self, sl_entries):
		if hasattr(self, 'supplied_items'):
			for d in self.get('supplied_items'):
				# negative quantity is passed, as raw material qty has to be decreased
				# when PR is submitted and it has to be increased when PR is cancelled
				incoming_rate = 0
				if self.is_return and self.return_against and self.docstatus == 1:
					incoming_rate = self.get_incoming_rate_for_sales_return(
						item_code=d.rm_item_code,
						warehouse=self.supplier_warehouse, batch_no=d.get('batch_no'),
						against_document_type=self.doctype, against_document=self.return_against
					)

				sl_entries.append(self.get_sl_entries(d, {
					"item_code": d.rm_item_code,
					"warehouse": self.supplier_warehouse,
					"actual_qty": -1 * flt(d.consumed_qty),
					"incoming_rate": incoming_rate
				}))

	def update_ordered_and_reserved_qty(self):
		po_map = {}
		for d in self.get("items"):
			if self.doctype in ("Purchase Receipt", "Purchase Invoice") and d.get("purchase_order") and d.get("purchase_order_item"):
				po_map.setdefault(d.purchase_order, []).append(d.purchase_order_item)

		for po, po_item_rows in po_map.items():
			po_obj = frappe.get_doc("Purchase Order", po)

			if po_obj.status in ["Closed", "Cancelled"] and not frappe.flags.ignored_closed_or_disabled:
				frappe.throw(_("{0} {1} is cancelled or closed").format(_("Purchase Order"), po),
					frappe.InvalidStatusError)

			po_obj.update_ordered_qty(po_item_rows)
			if self.get("is_subcontracted"):
				po_obj.update_reserved_qty_for_subcontract()

	def validate_budget(self):
		if self.docstatus == 1:
			for data in self.get('items'):
				args = data.as_dict()
				args.update({
					'doctype': self.doctype,
					'company': self.company,
					'posting_date': self.schedule_date if self.doctype == 'Material Request' else self.transaction_date
				})

				validate_expense_against_budget(args)

	def process_fixed_asset(self):
		if self.doctype == 'Purchase Invoice' and not self.update_stock:
			return

		asset_items = self.get_asset_items()
		if asset_items:
			self.auto_make_assets(asset_items)

	def auto_make_assets(self, asset_items):
		items_data = get_asset_item_details(asset_items)
		messages = []

		for d in self.items:
			if d.is_fixed_asset:
				item_data = items_data.get(d.item_code)

				if item_data.get('auto_create_assets'):
					# If asset has to be auto created
					# Check for asset naming series
					if item_data.get('asset_naming_series'):
						created_assets = []

						for qty in range(cint(d.qty)):
							asset = self.make_asset(d)
							created_assets.append(asset)

						if len(created_assets) > 5:
							# dont show asset form links if more than 5 assets are created
							messages.append(_('{} Assets created for {}').format(len(created_assets), frappe.bold(d.item_code)))
						else:
							assets_link = list(map(lambda d: frappe.utils.get_link_to_form('Asset', d), created_assets))
							assets_link = frappe.bold(','.join(assets_link))

							is_plural = 's' if len(created_assets) != 1 else ''
							messages.append(
								_('Asset{} {assets_link} created for {}').format(is_plural, frappe.bold(d.item_code), assets_link=assets_link)
							)
					else:
						frappe.throw(_("Row {}: Asset Naming Series is mandatory for the auto creation for item {}")
							.format(d.idx, frappe.bold(d.item_code)))
				else:
					messages.append(_("Assets not created for {0}. You will have to create asset manually.")
						.format(frappe.bold(d.item_code)))

		for message in messages:
			frappe.msgprint(message, title="Success", indicator="green")

	def make_asset(self, row):
		if not row.asset_location:
			frappe.throw(_("Row {0}: Enter location for the asset item {1}").format(row.idx, row.item_code))

		item_data = frappe.db.get_value('Item',
			row.item_code, ['asset_naming_series', 'asset_category'], as_dict=1)

		purchase_amount = flt(row.base_rate + row.item_tax_amount)
		asset = frappe.get_doc({
			'doctype': 'Asset',
			'item_code': row.item_code,
			'asset_name': row.item_name,
			'naming_series': item_data.get('asset_naming_series') or 'AST',
			'asset_category': item_data.get('asset_category'),
			'location': row.asset_location,
			'company': self.company,
			'supplier': self.supplier,
			'purchase_date': self.posting_date,
			'calculate_depreciation': 1,
			'purchase_receipt_amount': purchase_amount,
			'gross_purchase_amount': purchase_amount,
			'purchase_receipt': self.name if self.doctype == 'Purchase Receipt' else None,
			'purchase_invoice': self.name if self.doctype == 'Purchase Invoice' else None
		})

		asset.flags.ignore_validate = True
		asset.flags.ignore_mandatory = True
		asset.set_missing_values()
		asset.insert()

		return asset.name

	def update_fixed_asset(self, field, delete_asset = False):
		for d in self.get("items"):
			if d.is_fixed_asset:
				is_auto_create_enabled = frappe.db.get_value('Item', d.item_code, 'auto_create_assets')
				assets = frappe.db.get_all('Asset', filters={ field : self.name, 'item_code' : d.item_code })

				for asset in assets:
					asset = frappe.get_doc('Asset', asset.name)
					if delete_asset and is_auto_create_enabled:
						# need to delete movements to delete assets otherwise throws link exists error
						movements = frappe.db.sql(
							"""SELECT asm.name
							FROM `tabAsset Movement` asm, `tabAsset Movement Item` asm_item
							WHERE asm_item.parent=asm.name and asm_item.asset=%s""", asset.name, as_dict=1)
						for movement in movements:
							frappe.delete_doc('Asset Movement', movement.name, force=1)
						frappe.delete_doc("Asset", asset.name, force=1)
						continue

					if self.docstatus in [0, 1] and not asset.get(field):
						asset.set(field, self.name)
						asset.purchase_date = self.posting_date
						asset.supplier = self.supplier
					elif self.docstatus == 2:
						if asset.docstatus == 0:
							asset.set(field, None)
							asset.supplier = None
						if asset.docstatus == 1 and delete_asset:
							frappe.throw(_('Cannot cancel this document as it is linked with submitted asset {0}.\
								Please cancel the it to continue.').format(frappe.utils.get_link_to_form('Asset', asset.name)))

					asset.flags.ignore_validate_update_after_submit = True
					asset.flags.ignore_mandatory = True
					if asset.docstatus == 0:
						asset.flags.ignore_validate = True

					asset.save()

	def delete_linked_asset(self):
		if self.doctype == 'Purchase Invoice' and not self.get('update_stock'):
			return

		frappe.db.sql("delete from `tabAsset Movement` where reference_name=%s", self.name)

	def validate_schedule_date(self):
		if not self.get("items"):
			return

		schedule_dates = [getdate(d.schedule_date) for d in self.get("items") if d.get("schedule_date")]
		if schedule_dates:
			self.schedule_date = min(schedule_dates)

		if self.schedule_date:
			for d in self.get('items'):
				if not d.schedule_date:
					d.schedule_date = self.schedule_date

				if d.schedule_date and self.transaction_date and getdate(d.schedule_date) < getdate(self.transaction_date):
					frappe.throw(_("Row #{0}: Reqd by Date cannot be before Transaction Date").format(d.idx))
		else:
			if self.docstatus == 1:
				frappe.throw(_("Please enter Reqd by Date"))

	def validate_items(self):
		# validate items to see if they have is_purchase_item or is_subcontracted_item enabled
		if self.doctype == "Material Request":
			return

		if self.get("is_subcontracted"):
			if self.doctype == "Purchase Order":
				validate_item_type(self, "is_sub_contracted_item", "subcontracted")

			if self.doctype == "Purchase Receipt" or (self.doctype == "Purchase Invoice" and self.update_stock):
				validate_item_type(self, "is_purchase_item", "purchase", excluding=self.subcontracted_items)
		else:
			validate_item_type(self, "is_purchase_item", "purchase")


def get_asset_item_details(asset_items):
	asset_items_data = {}
	for d in frappe.get_all('Item', fields = ["name", "auto_create_assets", "asset_naming_series"],
		filters = {'name': ('in', asset_items)}):
		asset_items_data.setdefault(d.name, d)

	return asset_items_data


def validate_item_type(doc, fieldname, message, excluding=None):
	# iterate through items and check if they are valid sales or purchase items
	if not excluding:
		excluding = []

	items = [d.item_code for d in doc.items if d.item_code and d.item_code not in excluding]

	# No validation check inase of creating transaction using 'Opening Invoice Creation Tool'
	if not items:
		return

	invalid_items = []
	for item_code in items:
		if item_code not in invalid_items:
			if not frappe.get_cached_value("Item", item_code, fieldname):
				invalid_items.append(item_code)

	if invalid_items:
		invalid_items_message = ", ".join([frappe.utils.get_link_to_form("Item", item_code) for item_code in invalid_items])
		if len(invalid_items) > 1:
			error_message = _("The following Items {0} are not marked as {1} Item. You can enable them as {1} Item from Item Master")\
				.format(invalid_items_message, message)
		else:
			error_message = _("Item {0} is not marked as {1} Item. You can enable it as a {1} Item from Item Master")\
				.format(invalid_items_message, message)

		frappe.throw(error_message)
