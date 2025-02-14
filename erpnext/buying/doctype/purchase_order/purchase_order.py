# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import cstr, flt, cint
from frappe.model.mapper import get_mapped_doc
from erpnext.controllers.buying_controller import BuyingController
from erpnext.stock.stock_balance import update_bin_qty, get_ordered_qty
from frappe.desk.notifications import clear_doctype_notifications
from erpnext.buying.utils import validate_for_items, check_on_hold_or_closed_status
from erpnext.stock.utils import get_bin
from erpnext.accounts.party import get_party_account_currency
from erpnext.accounts.doctype.sales_invoice.sales_invoice import validate_inter_company_party, update_linked_doc,\
	unlink_inter_company_doc
import json


form_grid_templates = {
	"items": "templates/form_grid/item_grid.html"
}


class PurchaseOrder(BuyingController):
	def __init__(self, *args, **kwargs):
		super(PurchaseOrder, self).__init__(*args, **kwargs)
		self.status_map = [
			["Draft", None],
			["To Receive and Bill", "eval:self.receipt_status == 'To Receive' and self.billing_status == 'To Bill' and self.docstatus == 1"],
			["To Bill", "eval:self.receipt_status != 'To Receive' and self.billing_status == 'To Bill' and self.docstatus == 1"],
			["To Receive", "eval:self.receipt_status == 'To Receive' and self.billing_status != 'To Bill' and self.docstatus == 1"],
			["Completed", "eval:self.receipt_status != 'To Receive' and self.billing_status != 'To Bill' and self.docstatus == 1"],
			["Delivered", "eval:self.status == 'Delivered'"],
			["On Hold", "eval:self.status == 'On Hold'"],
			["Closed", "eval:self.status == 'Closed'"],
			["Cancelled", "eval:self.docstatus==2"],
		]

	def validate(self):
		super(PurchaseOrder, self).validate()

		self.validate_supplier()
		self.validate_schedule_date()
		validate_for_items(self)
		self.check_on_hold_or_closed_status()

		self.validate_uom_is_integer("uom", "qty")
		self.validate_uom_is_integer("stock_uom", "stock_qty")

		self.validate_minimum_order_qty()

		self.validate_for_subcontracting()
		self.set_raw_materials_supplied()

		validate_inter_company_party(self.doctype, self.supplier, self.company, self.inter_company_reference)

		self.validate_zero_amount()
		self.validate_with_previous_doc()
		self.set_advance_paid_amount()
		self.set_receipt_status()
		self.set_billing_status()
		self.set_raw_materials_supplied_qty()
		self.set_raw_materials_packed_qty()
		self.set_status()
		self.set_title()

	def before_submit(self):
		super().before_submit()
		self.validate_raw_materials_reserve_warehouse()

	def on_submit(self):
		super(PurchaseOrder, self).on_submit()

		self.update_previous_doc_status()
		self.update_requested_qty()
		self.update_ordered_qty()
		self.validate_budget()
		self.update_project_procurement_status()

		if self.is_subcontracted:
			self.update_reserved_qty_for_subcontract()

		frappe.get_doc('Authorization Control').validate_approving_authority(self.doctype,
			self.company, self.base_grand_total)

		self.update_blanket_order()

		update_linked_doc(self.doctype, self.name, self.inter_company_reference)

	def on_cancel(self):
		super(PurchaseOrder, self).on_cancel()
		self.unlink_payments_on_order_cancel()
		self.update_status_on_cancel()

		if self.has_drop_ship_item():
			self.update_delivered_qty_in_sales_order()

		if self.is_subcontracted:
			self.update_reserved_qty_for_subcontract()

		self.check_on_hold_or_closed_status()

		self.update_previous_doc_status()
		self.update_project_procurement_status()

		# Must be called after updating ordered qty in Material Request
		self.update_requested_qty()
		self.update_ordered_qty()

		self.update_blanket_order()

		unlink_inter_company_doc(self.doctype, self.name, self.inter_company_reference)

	def on_update(self):
		pass

	def on_gl_against_voucher(self, account, party_type, party, on_cancel):
		if not party_type or not party:
			return

		self.set_advance_paid_amount(update=True)
		self.set_status(update=True)
		self.notify_update()

	def set_title(self):
		self.title = self.supplier_name or self.supplier

	def update_previous_doc_status(self):
		material_requests = set()
		material_request_row_names = set()
		sales_orders = set()
		work_orders = set()

		for d in self.items:
			if d.material_request:
				material_requests.add(d.material_request)
			if d.material_request_item:
				material_request_row_names.add(d.material_request_item)
			if d.sales_order:
				sales_orders.add(d.sales_order)
			if d.work_order:
				work_orders.add(d.work_order)

		# Update Material Requests
		for name in material_requests:
			doc = frappe.get_doc("Material Request", name)
			doc.set_completion_status(update=True)
			doc.validate_ordered_qty(from_doctype=self.doctype, row_names=material_request_row_names)
			doc.set_status(update=True)
			doc.notify_update()

		# Update Sales Orders
		for name in sales_orders:
			doc = frappe.get_doc("Sales Order", name)
			doc.set_purchase_status(update=True)
			doc.set_status(update=True)
			doc.notify_update()

		# Update Work Orders
		for name in work_orders:
			doc = frappe.get_doc("Work Order", name)
			doc.run_method("update_status", from_doctype=self.doctype)
			doc.notify_update()

	def update_status(self, status):
		self.check_modified_date()
		self.set_status(status=status)
		self.set_receipt_status(update=True)
		self.set_billing_status(update=True)
		self.set_status(update=True, status=status)
		self.update_requested_qty()
		self.update_ordered_qty()
		self.update_project_procurement_status()
		if self.is_subcontracted:
			self.update_reserved_qty_for_subcontract()

		self.notify_update()
		clear_doctype_notifications(self)

	def set_advance_paid_amount(self, update=False, update_modified=True):
		from erpnext.accounts.utils import get_balance_on_voucher

		if self.docstatus != 0:
			party_type, party, party_name = self.get_billing_party()
			self.advance_paid = get_balance_on_voucher(self.doctype, self.name, party_type, party,
				account=None, company=self.company,
				dr_or_cr="debit_in_account_currency - credit_in_account_currency",
				include_original_references=True
			)
		else:
			self.advance_paid = 0

		if update:
			self.db_set("advance_paid", self.advance_paid, update_modified=update_modified)

	def validate_with_previous_doc(self):
		super(PurchaseOrder, self).validate_with_previous_doc({
			"Supplier Quotation": {
				"ref_dn_field": "supplier_quotation",
				"compare_fields": [["supplier", "="], ["company", "="], ["currency", "="]],
			},
			"Supplier Quotation Item": {
				"ref_dn_field": "supplier_quotation_item",
				"compare_fields": [["project", "="], ["item_code", "="],
					["uom", "="], ["conversion_factor", "="]],
				"is_child_table": True
			},
			"Material Request": {
				"ref_dn_field": "material_request",
				"compare_fields": [["company", "="]],
			},
			"Material Request Item": {
				"ref_dn_field": "material_request_item",
				"compare_fields": [["item_code", "="]],
				"is_child_table": True
			}
		})

		self.validate_work_orders()

		if cint(frappe.get_cached_value('Buying Settings', None, 'maintain_same_rate')):
			self.validate_rate_with_reference_doc([["Supplier Quotation", "supplier_quotation", "supplier_quotation_item"]])

	def set_receipt_status(self, update=False, update_modified=True):
		data = self.get_receipt_status_data()

		# update values in rows
		for d in self.items:
			d.received_qty = max(flt(data.received_qty_map.get(d.name)), flt(data.service_billed_qty_map.get(d.name)))
			d.total_returned_qty = flt(data.total_returned_qty_map.get(d.name))

			if update:
				d.db_set({
					'received_qty': d.received_qty,
					'total_returned_qty': d.total_returned_qty,
				}, update_modified=update_modified)

		# update percentage in parent
		self.per_received = self.calculate_status_percentage('received_qty', 'qty', data.receivable_rows)
		if self.per_received is None:
			self.per_received = flt(self.calculate_status_percentage('received_qty', 'qty', self.items))

		# update delivery_status
		self.receipt_status = self.get_completion_status('per_received', 'Receive',
			not_applicable=self.status == "Closed")

		if update:
			self.db_set({
				'per_received': self.per_received,
				'receipt_status': self.receipt_status,
			}, update_modified=update_modified)

	def set_billing_status(self, update=False, update_modified=True):
		data = self.get_billing_status_data()

		# update values in rows
		for d in self.items:
			d.billed_qty = flt(data.billed_qty_map.get(d.name))
			d.billed_amt = flt(data.billed_amount_map.get(d.name))
			d.returned_qty = flt(data.receipt_return_qty_map.get(d.name))
			if update:
				d.db_set({
					'billed_qty': d.billed_qty,
					'billed_amt': d.billed_amt,
					'returned_qty': d.returned_qty,
				}, update_modified=update_modified)

		# update percentage in parent
		self.per_returned = flt(self.calculate_status_percentage('returned_qty', 'qty', self.items))
		self.per_billed = self.calculate_status_percentage('billed_qty', 'qty', self.items)
		self.per_completed = self.calculate_status_percentage(['billed_qty', 'returned_qty'], 'qty', self.items)
		if self.per_completed is None:
			total_billed_qty = flt(sum([flt(d.billed_qty) for d in self.items]), self.precision('total_qty'))
			self.per_billed = 100 if total_billed_qty else 0
			self.per_completed = 100 if total_billed_qty else 0

		receipts_not_billable = self.receipt_status == "Received" and not data.has_unbilled_receipt
		unreceivable_rows_billed = all(d.billed_qty >= d.qty for d in data.unreceivable_rows)
		not_billable = receipts_not_billable and unreceivable_rows_billed
		self.billing_status = self.get_completion_status('per_completed', 'Bill',
			not_applicable=self.status == "Closed" or self.per_returned == 100 or (not_billable and not self.per_billed),
			not_applicable_based_on='per_billed',
			within_allowance=self.per_billed and not_billable)

		if update:
			self.db_set({
				'per_billed': self.per_billed,
				'per_returned': self.per_returned,
				'per_completed': self.per_completed,
				'billing_status': self.billing_status,
			}, update_modified=update_modified)

	def get_receipt_status_data(self):
		out = frappe._dict()

		out.receivable_rows = []
		out.received_qty_map = {}
		out.total_returned_qty_map = {}
		out.service_billed_qty_map = {}

		reveived_by_prec_row_names = []
		received_by_billing_row_names = []

		for d in self.items:
			is_receivable = d.is_stock_item or d.is_fixed_asset
			if is_receivable:
				out.receivable_rows.append(d)

				if d.delivered_by_supplier:
					out.received_qty_map[d.name] = d.qty
				else:
					reveived_by_prec_row_names.append(d.name)
			else:
				received_by_billing_row_names.append(d.name)
				reveived_by_prec_row_names.append(d.name)

		# Get Received Qty
		if self.docstatus == 1:
			if reveived_by_prec_row_names:
				# Received By Purchase Receipt
				recieved_by_prec = frappe.db.sql("""
					select i.purchase_order_item, i.received_qty, p.is_return, p.reopen_order
					from `tabPurchase Receipt Item` i
					inner join `tabPurchase Receipt` p on p.name = i.parent
					where p.docstatus = 1 and i.purchase_order_item in %s
				""", [reveived_by_prec_row_names], as_dict=1)

				for d in recieved_by_prec:
					if not d.is_return or d.reopen_order:
						out.received_qty_map.setdefault(d.purchase_order_item, 0)
						out.received_qty_map[d.purchase_order_item] += d.received_qty

					if d.is_return:
						out.total_returned_qty_map.setdefault(d.purchase_order_item, 0)
						out.total_returned_qty_map[d.purchase_order_item] -= d.received_qty

				# Received By Purchase Invoice
				received_by_pinv = frappe.db.sql("""
					select i.purchase_order_item, i.received_qty, p.is_return, p.reopen_order
					from `tabPurchase Invoice Item` i
					inner join `tabPurchase Invoice` p on p.name = i.parent
					where p.docstatus = 1 and p.update_stock = 1 and i.purchase_order_item in %s
				""", [reveived_by_prec_row_names], as_dict=1)

				for d in received_by_pinv:
					if not d.is_return or d.reopen_order:
						out.received_qty_map.setdefault(d.purchase_order_item, 0)
						out.received_qty_map[d.purchase_order_item] += d.received_qty

					if d.is_return:
						out.total_returned_qty_map.setdefault(d.purchase_order_item, 0)
						out.total_returned_qty_map[d.purchase_order_item] -= d.received_qty

			# Get Service Items Billed Qty as Delivered Qty
			if received_by_billing_row_names:
				out.service_billed_qty_map = dict(frappe.db.sql("""
					select i.purchase_order_item, sum(i.qty)
					from `tabPurchase Invoice Item` i
					inner join `tabPurchase Invoice` p on p.name = i.parent
					where p.docstatus = 1 and (p.is_return = 0 or p.reopen_order = 1)
						and i.purchase_order_item in %s
					group by i.purchase_order_item
				""", [received_by_billing_row_names]))

		return out

	def get_billing_status_data(self):
		out = frappe._dict()
		out.unreceivable_rows = []
		out.billed_qty_map = {}
		out.billed_amount_map = {}
		out.receipt_return_qty_map = {}
		out.has_unbilled_receipt = False

		for d in self.items:
			is_receivable = d.is_stock_item or d.is_fixed_asset
			if not is_receivable:
				out.unreceivable_rows.append(d)

		if self.docstatus == 1:
			row_names = [d.name for d in self.items]
			if row_names:
				# Billed By Purchase Invoice
				billed_by_pinv = frappe.db.sql("""
					select i.purchase_order_item, i.qty, i.amount, p.is_return, p.reopen_order
					from `tabPurchase Invoice Item` i
					inner join `tabPurchase Invoice` p on p.name = i.parent
					where p.docstatus = 1 and (p.is_return = 0 or p.reopen_order = 1)
						and i.purchase_order_item in %s
				""", [row_names], as_dict=1)

				for d in billed_by_pinv:
					out.billed_amount_map.setdefault(d.purchase_order_item, 0)
					out.billed_amount_map[d.purchase_order_item] += d.amount

					out.billed_qty_map.setdefault(d.purchase_order_item, 0)
					out.billed_qty_map[d.purchase_order_item] += d.qty

				# Returned By Purchase Receipt
				received_by_prec = frappe.db.sql("""
					select i.purchase_order_item, i.qty, p.is_return, p.reopen_order, p.billing_status
					from `tabPurchase Receipt Item` i
					inner join `tabPurchase Receipt` p on p.name = i.parent
					where p.docstatus = 1 and i.purchase_order_item in %s
				""", [row_names], as_dict=1)

				for d in received_by_prec:
					if d.is_return and not d.reopen_order:
						out.receipt_return_qty_map.setdefault(d.purchase_order_item, 0)
						out.receipt_return_qty_map[d.purchase_order_item] -= d.qty

					if d.billing_status == "To Bill":
						out.has_unbilled_receipt = True

		return out

	def validate_received_qty(self, from_doctype=None, row_names=None):
		self.validate_completed_qty('received_qty', 'qty', self.items,
			allowance_type='qty', from_doctype=from_doctype, row_names=row_names)

	def validate_billed_qty(self, from_doctype=None, row_names=None):
		self.validate_completed_qty(['billed_qty', 'returned_qty'], 'qty', self.items,
			allowance_type='billing', from_doctype=from_doctype, row_names=row_names)

		if frappe.get_cached_value("Accounts Settings", None, "validate_over_billing_in_sales_invoice"):
			self.validate_completed_qty('billed_amt', 'amount', self.items,
				allowance_type='billing', from_doctype=from_doctype, row_names=row_names)

	def set_raw_materials_supplied_qty(self, update=False, update_modified=True):
		supplied_qty_map = self.get_raw_materials_supplied_qty_map()

		for d in self.get("supplied_items"):
			key = (d.rm_item_code, d.main_item_code)
			last_rm_of_key = [x for x in self.get("supplied_items") if (x.rm_item_code, x.main_item_code) == key][-1]

			d.supplied_qty = flt(supplied_qty_map.get(key))
			if d != last_rm_of_key and key in supplied_qty_map:
				d.supplied_qty = min(d.required_qty, d.supplied_qty)
				supplied_qty_map[key] -= d.supplied_qty

			if update:
				d.db_set("supplied_qty", d.supplied_qty, update_modified=update_modified)

	def get_raw_materials_supplied_qty_map(self):
		supplied_qty_map = {}

		if self.docstatus == 1:
			subcontract_item_codes = [d.main_item_code for d in self.get("supplied_items")]
			if subcontract_item_codes:
				supplied_qty_data = frappe.db.sql("""
					select
						if(i.original_item != '' and i.original_item is not null, i.original_item, i.item_code) as rm_item_code,
						i.subcontracted_item as main_item_code,
						sum(i.stock_qty) as supplied_qty
					from `tabStock Entry Detail` i
					inner join `tabStock Entry` ste on ste.name = i.parent
					where ste.docstatus = 1 and ste.purpose = 'Send to Subcontractor'
						and ste.purchase_order = %s and i.subcontracted_item in %s
					group by rm_item_code, main_item_code
				""", (self.name, subcontract_item_codes), as_dict=1)

				for d in supplied_qty_data:
					supplied_qty_map[(d.rm_item_code, d.main_item_code)] = d.supplied_qty

		return supplied_qty_map

	def set_raw_materials_packed_qty(self, update=False, update_modified=True):
		packed_qty_map = self.get_raw_materials_packed_qty_map()

		for d in self.get("supplied_items"):
			key = (d.rm_item_code, d.main_item_code)
			last_rm_of_key = [x for x in self.get("supplied_items") if (x.rm_item_code, x.main_item_code) == key][-1]

			d.packed_qty = flt(packed_qty_map.get(key))
			if d != last_rm_of_key and key in packed_qty_map:
				d.packed_qty = min(d.required_qty, d.packed_qty)
				packed_qty_map[key] -= d.packed_qty

			if update:
				d.db_set("packed_qty", d.packed_qty, update_modified=update_modified)

	def get_raw_materials_packed_qty_map(self):
		packed_qty_map = {}

		if self.docstatus == 1:
			subcontract_item_codes = [d.main_item_code for d in self.get("supplied_items")]
			if subcontract_item_codes:
				packed_qty_data = frappe.db.sql("""
					select
						i.item_code as rm_item_code,
						i.subcontracted_item as main_item_code,
						sum(i.stock_qty) as packed_qty
					from `tabPacking Slip Item` i
					inner join `tabPacking Slip` ps on ps.name = i.parent
					where ps.docstatus = 1
						and ps.purchase_order = %s
						and ifnull(i.source_packing_slip, '') = ''
						and i.subcontracted_item in %s
						and i.qty != 0
					group by rm_item_code, main_item_code
				""", (self.name, subcontract_item_codes), as_dict=1)

				for d in packed_qty_data:
					packed_qty_map[(d.rm_item_code, d.main_item_code)] = d.packed_qty

		return packed_qty_map

	def update_delivered_qty_in_sales_order(self):
		"""Update delivered qty in Sales Order for drop ship"""
		sales_orders_to_update = []
		for item in self.items:
			if item.sales_order and item.delivered_by_supplier == 1:
				if item.sales_order not in sales_orders_to_update:
					sales_orders_to_update.append(item.sales_order)

		for so_name in sales_orders_to_update:
			so = frappe.get_doc("Sales Order", so_name)
			so.set_delivery_status(update=True)
			so.set_status(update=True)
			so.notify_update()

	def validate_supplier(self):
		prevent_po = frappe.get_cached_value("Supplier", self.supplier, 'prevent_pos')
		if prevent_po:
			standing = frappe.get_cached_value("Supplier Scorecard", self.supplier, 'status')
			if standing:
				frappe.throw(_("Purchase Orders are not allowed for {0} due to a scorecard standing of {1}.")
					.format(self.supplier, standing))

		warn_po = frappe.get_cached_value("Supplier", self.supplier, 'warn_pos')
		if warn_po:
			standing = frappe.get_cached_value("Supplier Scorecard", self.supplier, 'status')
			frappe.msgprint(_("{0} currently has a {1} Supplier Scorecard standing, and Purchase Orders to this supplier should be issued with caution.")
				.format(self.supplier, standing), title=_("Caution"), indicator='orange')

		self.party_account_currency = get_party_account_currency("Supplier", self.supplier, self.company)

	def validate_minimum_order_qty(self):
		if not self.get("items"):
			return

		items = list(set([d.item_code for d in self.get("items")]))

		itemwise_min_order_qty = frappe._dict(frappe.db.sql("""select name, min_order_qty
			from tabItem where name in ({0})""".format(", ".join(["%s"] * len(items))), items))

		itemwise_qty = frappe._dict()
		for d in self.get("items"):
			itemwise_qty.setdefault(d.item_code, 0)
			itemwise_qty[d.item_code] += flt(d.stock_qty)

		for item_code, qty in itemwise_qty.items():
			if flt(qty) < flt(itemwise_min_order_qty.get(item_code)):
				frappe.throw(_("Item {0}: Ordered qty {1} cannot be less than minimum order qty {2} (defined in Item).")
					.format(item_code, qty, itemwise_min_order_qty.get(item_code)))

	def validate_raw_materials_reserve_warehouse(self):
		if self.is_subcontracted:
			for supplied_item in self.get("supplied_items"):
				if not supplied_item.reserve_warehouse:
					frappe.throw(_("Row #{0}: Reserve Warehouse is mandatory for Material Item {0} in Raw Materials supplied").format(
						supplied_item.idx, frappe.bold(supplied_item.rm_item_code)
					))

	def get_schedule_dates(self):
		for d in self.get('items'):
			if d.material_request_item and not d.schedule_date:
				d.schedule_date = frappe.db.get_value("Material Request Item",
						d.material_request_item, "schedule_date")

	@frappe.whitelist()
	def get_last_purchase_rate(self):
		from erpnext.stock.get_item_details import get_price_from_last_purchase
		"""get last purchase rates for all items"""

		for d in self.get("items"):
			if not d.item_code:
				continue

			last_purchase_details = get_price_from_last_purchase(
				d.item_code,
				uom=d.uom,
				conversion_factor=d.get("conversion_factor"),
				exchange_rate=self.get("conversion_rate"),
				exclude=self.name
			)

			if last_purchase_details:
				d.price_list_rate = last_purchase_details.price_list_rate
				d.rate = last_purchase_details.rate
				d.last_purchase_rate = last_purchase_details.last_purchase_rate
			else:
				item_last_purchase_rate = flt(frappe.get_cached_value("Item", d.item_code, "last_purchase_rate"))
				if item_last_purchase_rate:
					conversion_factor = flt(d.get("conversion_factor")) or 1
					exchange_rate = flt(self.get("conversion_rate")) or 1
					d.price_list_rate = d.rate = d.last_purchase_rate = item_last_purchase_rate / conversion_factor / exchange_rate

	# Check for Closed status
	def check_on_hold_or_closed_status(self):
		check_list = []
		for d in self.get('items'):
			if d.meta.get_field('material_request') and d.material_request and d.material_request not in check_list:
				check_list.append(d.material_request)
				check_on_hold_or_closed_status('Material Request', d.material_request)

	def update_requested_qty(self):
		material_request_map = {}
		for d in self.get("items"):
			if d.material_request_item:
				material_request_map.setdefault(d.material_request, []).append(d.material_request_item)

		for mr, mr_item_rows in material_request_map.items():
			if mr and mr_item_rows:
				mr_obj = frappe.get_doc("Material Request", mr)

				if mr_obj.status in ["Stopped", "Cancelled"]:
					frappe.throw(_("Material Request {0} is cancelled or stopped").format(mr), frappe.InvalidStatusError)

				mr_obj.update_requested_qty(mr_item_rows)

	def update_ordered_qty(self, po_item_rows=None):
		"""update requested qty (before ordered_qty is updated)"""
		item_wh_list = []
		for d in self.get("items"):
			if (
				(not po_item_rows or d.name in po_item_rows)
				and [d.item_code, d.warehouse] not in item_wh_list
				and d.warehouse
				and not d.delivered_by_supplier
				and frappe.db.get_value("Item", d.item_code, "is_stock_item", cache=1)
			):
				item_wh_list.append([d.item_code, d.warehouse])

		for item_code, warehouse in item_wh_list:
			update_bin_qty(item_code, warehouse, {
				"ordered_qty": get_ordered_qty(item_code, warehouse)
			})

	def check_modified_date(self):
		mod_db = frappe.db.sql("select modified from `tabPurchase Order` where name = %s",
			self.name)
		date_diff = frappe.db.sql("select '%s' - '%s' " % (mod_db[0][0], cstr(self.modified)))

		if date_diff and date_diff[0][0]:
			frappe.msgprint(_("{0} {1} has been modified. Please refresh.").format(self.doctype, self.name),
				raise_exception=True)

	def has_drop_ship_item(self):
		return any([d.delivered_by_supplier for d in self.items])

	def is_against_so(self):
		return any([d.sales_order for d in self.items if d.sales_order])

	def update_reserved_qty_for_subcontract(self):
		bins = []

		for d in self.supplied_items:
			b = (d.rm_item_code, d.reserve_warehouse)
			if b not in bins:
				bins.append(b)

		for b in bins:
			stock_bin = get_bin(b[0], b[1])
			stock_bin.update_reserved_qty_for_sub_contracting()


@frappe.whitelist()
def close_or_unclose_purchase_orders(names, status):
	if not frappe.has_permission("Purchase Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	names = json.loads(names)
	for name in names:
		po = frappe.get_doc("Purchase Order", name)
		if po.docstatus == 1:
			if status == "Closed":
				if po.status not in ("Cancelled", "Closed") and (po.receipt_status == 'To Receive' or po.billing_status == 'To Bill'):
					po.run_method("update_status", status)
			else:
				if po.status == "Closed":
					po.run_method("update_status", "Draft")
			po.update_blanket_order()

	frappe.local.message_log = []


def set_missing_values(source, target):
	from erpnext.vehicles.doctype.vehicle.vehicle import split_vehicle_items_by_qty, set_reserved_vehicles_from_po
	split_vehicle_items_by_qty(target)
	set_reserved_vehicles_from_po(source, target)

	target.ignore_pricing_rule = 1
	target.run_method("postprocess_after_mapping")


@frappe.whitelist()
def make_purchase_receipt(source_name, target_doc=None, warehouse=None):
	if not warehouse and frappe.flags.args:
		warehouse = frappe.flags.args.warehouse

	def postprocess(source, target):
		if warehouse:
			target.set_warehouse = warehouse

		set_missing_values(source, target)

	def get_pending_qty(source):
		return flt(source.qty) - flt(source.received_qty)

	def item_condition(source, source_parent, target_parent):
		if source.name in [d.purchase_order_item for d in target_parent.get('items') if d.purchase_order_item]:
			return False

		if source.delivered_by_supplier:
			return False

		# if not source.is_stock_item and not source.is_fixed_asset:
		# 	return False

		return abs(source.received_qty) < abs(source.qty)

	def update_item(source, target, source_parent, target_parent):
		target.qty = get_pending_qty(source)
		target.received_qty = target.qty + flt(target.rejected_qty)

	mapper = {
		"Purchase Order": {
			"doctype": "Purchase Receipt",
			"field_map": {
				"supplier_warehouse": "supplier_warehouse",
				"remarks": "remarks"
			},
			"validation": {
				"docstatus": ["=", 1],
			}
		},
		"Purchase Order Item": {
			"doctype": "Purchase Receipt Item",
			"field_map": {
				"name": "purchase_order_item",
				"parent": "purchase_order",
				"bom": "bom",
				"material_request": "material_request",
				"material_request_item": "material_request_item",
				"work_order": "work_order",
			},
			"postprocess": update_item,
			"condition": item_condition,
		},
		"Purchase Taxes and Charges": {
			"doctype": "Purchase Taxes and Charges",
			"add_if_empty": True
		},
		"postprocess": postprocess,
	}

	frappe.utils.call_hook_method("update_purchase_receipt_from_purchase_order_mapper", mapper, "Purchase Receipt")

	doc = get_mapped_doc("Purchase Order", source_name,	mapper, target_doc)

	return doc


@frappe.whitelist()
def make_purchase_invoice(source_name, target_doc=None):
	return get_mapped_purchase_invoice(source_name, target_doc)


@frappe.whitelist()
def make_purchase_invoice_from_portal(purchase_order_name):
	doc = get_mapped_purchase_invoice(purchase_order_name, ignore_permissions=True)
	if doc.contact_email != frappe.session.user:
		frappe.throw(_('Not Permitted'), frappe.PermissionError)
	doc.save()
	frappe.db.commit()
	frappe.response['type'] = 'redirect'
	frappe.response.location = '/purchase-invoices/' + doc.name


def get_mapped_purchase_invoice(source_name, target_doc=None, ignore_permissions=False):
	unbilled_pr_qty_map = get_unbilled_pr_qty_map(source_name)

	def get_pending_qty(source):
		billable_qty = flt(source.qty) - flt(source.billed_qty) - flt(source.returned_qty)
		unbilled_pr_qty = flt(unbilled_pr_qty_map.get(source.name))
		return max(billable_qty - unbilled_pr_qty, 0)

	def item_condition(source, source_parent, target_parent):
		if source.name in [d.purchase_order_item for d in target_parent.get('items') if d.purchase_order_item and not d.purchase_receipt_item]:
			return False

		return get_pending_qty(source)

	def update_item(source, target, source_parent, target_parent):
		target.qty = get_pending_qty(source)

	def postprocess(source, target):
		target.flags.ignore_permissions = ignore_permissions
		set_missing_values(source, target)

		if target.get("allocate_advances_automatically"):
			target.set_advances()

	mapper = {
		"Purchase Order": {
			"doctype": "Purchase Invoice",
			"field_map": {
				"party_account_currency": "party_account_currency",
				"supplier_warehouse":"supplier_warehouse",
				"remarks": "remarks",
			},
			"validation": {
				"docstatus": ["=", 1],
			}
		},
		"Purchase Order Item": {
			"doctype": "Purchase Invoice Item",
			"field_map": {
				"name": "purchase_order_item",
				"parent": "purchase_order",
				"work_order": "work_order",
				"material_request": "material_request",
				"material_request_item": "material_request_item",
			},
			"postprocess": update_item,
			"condition": item_condition,
		},
		"Purchase Taxes and Charges": {
			"doctype": "Purchase Taxes and Charges",
			"add_if_empty": True
		},
		"postprocess": postprocess,
	}

	if frappe.get_single("Accounts Settings").automatically_fetch_payment_terms == 1:
		mapper["Payment Schedule"] = {
			"doctype": "Payment Schedule",
			"add_if_empty": True
		}

	frappe.utils.call_hook_method("update_purchase_invoice_from_purchase_order_mapper", mapper, "Purchase Invoice")

	doc = get_mapped_doc("Purchase Order", source_name,	mapper,
		target_doc, ignore_permissions=ignore_permissions)

	return doc


def get_unbilled_pr_qty_map(purchase_order):
	unbilled_pr_qty_map = {}

	item_data = frappe.db.sql("""
		select purchase_order_item, qty - billed_qty
		from `tabPurchase Receipt Item`
		where purchase_order=%s and docstatus=1
	""", purchase_order)

	for purchase_receipt_item, qty in item_data:
		if not unbilled_pr_qty_map.get(purchase_receipt_item):
			unbilled_pr_qty_map[purchase_receipt_item] = 0
		unbilled_pr_qty_map[purchase_receipt_item] += qty

	return unbilled_pr_qty_map


@frappe.whitelist()
def make_rm_stock_entry(purchase_order, packing_slips=None):
	purchase_order = frappe.get_doc("Purchase Order", purchase_order)
	supplied_items = get_pending_raw_materials_to_transfer(purchase_order)

	ste = frappe.new_doc("Stock Entry")
	ste.company = purchase_order.company
	ste.purpose = "Send to Subcontractor"
	ste.purchase_order = purchase_order.name
	ste.supplier = purchase_order.supplier
	ste.supplier_name = purchase_order.supplier_name
	ste.supplier_address = purchase_order.supplier_address
	ste.address_display = purchase_order.address_display
	ste.from_warehouse = purchase_order.set_reserve_warehouse
	ste.to_warehouse = purchase_order.supplier_warehouse
	ste.cost_center = purchase_order.get("cost_center")
	ste.set_stock_entry_type()

	if packing_slips:
		if isinstance(packing_slips, str):
			packing_slips = json.loads(packing_slips)
	else:
		packing_slips = frappe.get_all("Packing Slip", filters={
			"purchase_order": purchase_order.name,
			"status": "In Stock",
			"docstatus": 1,
		}, pluck="name", order_by="posting_date, posting_time, creation")

	if packing_slips:
		from erpnext.stock.doctype.packing_slip.packing_slip import map_stock_entry_items
		for name in packing_slips:
			ps = frappe.get_doc("Packing Slip", name)
			map_stock_entry_items(ps, ste, target_warehouse=purchase_order.supplier_warehouse)
	else:
		for d in supplied_items:
			ste.add_to_stock_entry_detail({
				d.rm_item_code: {
					"item_code": d.rm_item_code,
					"from_warehouse": d.reserve_warehouse,
					"to_warehouse": purchase_order.supplier_warehouse,
					"subcontracted_item": d.main_item_code,
					"purchase_order_item": d.name,
					"qty": flt(d.required_qty) - flt(d.supplied_qty),
					"uom": d.stock_uom,
				}
			})

	ste.run_method("postprocess_after_mapping")
	return ste.as_dict()


@frappe.whitelist()
def make_packing_slip(purchase_order):
	purchase_order = frappe.get_doc("Purchase Order", purchase_order)
	supplied_items = get_pending_raw_materials_to_transfer(purchase_order)

	doc = frappe.new_doc("Packing Slip")
	doc.company = purchase_order.company
	doc.purchase_order = purchase_order.name
	doc.supplier = purchase_order.supplier
	doc.supplier_name = purchase_order.supplier_name
	doc.from_warehouse = purchase_order.set_reserve_warehouse
	doc.target_warehouse = purchase_order.set_reserve_warehouse

	if purchase_order.meta.has_field("cost_center") and doc.meta.has_field("cost_center"):
		doc.cost_center = purchase_order.cost_center

	for d in supplied_items:
		unsupplied_qty = flt(d.required_qty) - flt(d.supplied_qty)
		unpacked_qty = flt(d.required_qty) - flt(d.packed_qty)
		to_pack = min(unpacked_qty, unsupplied_qty)
		to_pack = max(to_pack, 0)

		row = doc.append("items", frappe.new_doc("Packing Slip Item"))
		row.update({
			"item_code": d.rm_item_code,
			"source_warehouse": d.reserve_warehouse,
			"subcontracted_item": d.main_item_code,
			"purchase_order_item": d.name,
			"qty": to_pack,
			"uom": d.stock_uom,
		})

	doc.run_method("set_target_warehouse_as_source_warehouse")
	doc.run_method("postprocess_after_mapping")

	return doc


def get_pending_raw_materials_to_transfer(purchase_order):
	if purchase_order.docstatus != 1:
		frappe.throw(_("Purchase Order {0} not submitted").format(purchase_order.name))
	if not purchase_order.is_subcontracted:
		frappe.throw(_("Purchase Order {0} is not a subcontracted order").format(purchase_order.name))

	supplied_items = [d for d in purchase_order.supplied_items if
		flt(d.supplied_qty, d.precision("required_qty")) < flt(d.required_qty, d.precision("required_qty"))]

	if not supplied_items:
		frappe.throw(_("No raw materials to transfer"))

	return supplied_items


@frappe.whitelist()
def update_status(status, name):
	po = frappe.get_doc("Purchase Order", name)
	po.update_status(status)
	po.update_delivered_qty_in_sales_order()


@frappe.whitelist()
def make_inter_company_sales_order(source_name, target_doc=None):
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_inter_company_transaction
	return make_inter_company_transaction("Purchase Order", source_name, target_doc)


def get_subcontracted_item_from_material_item(material_item, purchase_order):
	out = frappe._dict()
	if not purchase_order or not material_item:
		return out

	subcontract_item_codes = frappe.get_all("Purchase Order Item Supplied",
		{"parent": purchase_order, "rm_item_code": material_item}, pluck="main_item_code")
	subcontract_item_codes = list(set(subcontract_item_codes))

	if not subcontract_item_codes:
		subcontract_item_codes = frappe.db.sql_list("""
			select distinct po_item.item_code
			from `tabPurchase Order Item` po_item
			inner join `tabItem` i on i.name = po_item.item_code
			where po_item.parent = %s and i.is_sub_contracted_item = 1
		""", purchase_order)

	if subcontract_item_codes and len(subcontract_item_codes) == 1:
		out["subcontracted_item"] = subcontract_item_codes[0]
		out["subcontracted_item_name"] = frappe.get_cached_value("Item", out.subcontracted_item, "item_name")

	return out
