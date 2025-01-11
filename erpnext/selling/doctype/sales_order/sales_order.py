# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
import json
import frappe.utils
from frappe.utils import cstr, flt, getdate, cint, nowdate, add_days, get_link_to_form, round_up, round_down
from frappe import _
from frappe.model.mapper import get_mapped_doc
from erpnext.stock.stock_balance import update_bin_qty, get_reserved_qty
from frappe.desk.notifications import clear_doctype_notifications
from erpnext.controllers.selling_controller import SellingController
from erpnext.controllers.transaction_controller import get_default_taxes_and_charges
from erpnext.vehicles.doctype.vehicle.vehicle import split_vehicle_items_by_qty, set_reserved_vehicles_from_so
from erpnext.selling.doctype.customer.customer import check_credit_limit
from erpnext.manufacturing.doctype.production_plan.production_plan import get_items_for_material_requests
from erpnext.accounts.doctype.sales_invoice.sales_invoice import validate_inter_company_party, update_linked_doc
from erpnext.stock.get_item_details import item_has_product_bundle, get_skip_delivery_note, get_default_bom
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos


form_grid_templates = {
	"items": "templates/form_grid/item_grid.html"
}


class WarehouseRequired(frappe.ValidationError):
	pass


class SalesOrder(SellingController):
	def __init__(self, *args, **kwargs):
		super(SalesOrder, self).__init__(*args, **kwargs)
		self.status_map = [
			["Draft", None],
			["To Deliver and Bill", "eval:self.delivery_status == 'To Deliver' and self.billing_status == 'To Bill' and self.docstatus == 1"],
			["To Bill", "eval:self.delivery_status != 'To Deliver' and self.billing_status == 'To Bill' and self.docstatus == 1"],
			["To Deliver", "eval:self.delivery_status == 'To Deliver' and self.billing_status != 'To Bill' and self.docstatus == 1"],
			["Completed", "eval:self.delivery_status != 'To Deliver' and self.billing_status != 'To Bill' and self.docstatus == 1"],
			["Closed", "eval:self.status == 'Closed'"],
			["On Hold", "eval:self.status == 'On Hold'"],
			["Cancelled", "eval:self.docstatus==2"],
		]

	def validate(self):
		super(SalesOrder, self).validate()
		self.validate_delivery_date()
		self.validate_po()
		self.validate_project_customer()
		self.validate_uom_is_integer("stock_uom", "stock_qty")
		self.validate_uom_is_integer("uom", "qty")
		self.validate_for_items()
		self.validate_warehouse()
		self.validate_drop_ship()
		self.validate_serial_no_based_delivery()
		validate_inter_company_party(self.doctype, self.customer, self.company, self.inter_company_reference)

		if self.coupon_code:
			from erpnext.accounts.doctype.pricing_rule.utils import validate_coupon_code
			validate_coupon_code(self.coupon_code)

		from erpnext.stock.doctype.packed_item.packed_item import make_packing_list
		make_packing_list(self)

		self.validate_with_previous_doc()
		self.set_advance_paid_amount()
		self.set_delivery_status()
		self.set_production_packing_status()
		self.set_billing_status()
		self.set_purchase_status()
		self.set_status()
		self.set_title()

	def before_submit(self):
		self.validate_delivery_date_required()
		self.validate_item_code_mandatory()
		self.validate_previous_docstatus()

	def on_submit(self):
		self.check_credit_limit()
		self.update_reserved_qty()

		frappe.get_doc('Authorization Control').validate_approving_authority(self.doctype, self.company, self.base_grand_total, self)
		self.update_previous_doc_status()

		self.update_blanket_order()

		update_linked_doc(self.doctype, self.name, self.inter_company_reference)
		if self.coupon_code:
			from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count
			update_coupon_code_count(self.coupon_code, 'used')

	def on_cancel(self):
		self.unlink_payments_on_order_cancel()
		self.update_status_on_cancel()

		# Cannot cancel closed SO
		if self.status == 'Closed':
			frappe.throw(_("Closed order cannot be cancelled. Unclose to cancel."))

		self.check_nextdoc_docstatus()
		self.update_reserved_qty()
		self.update_previous_doc_status()

		self.update_blanket_order()

		if self.coupon_code:
			from erpnext.accounts.doctype.pricing_rule.utils import update_coupon_code_count
			update_coupon_code_count(self.coupon_code, 'cancelled')

	def before_update_after_submit(self):
		super(SalesOrder, self).before_update_after_submit()
		self.validate_po()
		self.validate_drop_ship()
		self.validate_supplier_after_submit()
		self.validate_delivery_date()

	def on_gl_against_voucher(self, account, party_type, party, on_cancel):
		if not party_type or not party:
			return

		self.set_advance_paid_amount(update=True)
		self.set_status(update=True)
		self.notify_update()

	def set_title(self):
		self.title = self.customer_name or self.customer

	def update_status(self, status):
		self.check_modified_date()
		self.set_status(status=status)
		self.set_delivery_status(update=True)
		self.set_production_packing_status(update=True)
		self.set_billing_status(update=True)
		self.set_status(update=True, status=status)
		self.update_project_billing_and_sales()
		self.update_reserved_qty()
		self.notify_update()
		clear_doctype_notifications(self)

	def set_missing_values(self, for_validate=False):
		super().set_missing_values(for_validate=for_validate)
		self.set_skip_delivery_note()

	def set_skip_delivery_note(self):
		for d in self.get("items"):
			self.set_skip_delivery_note_for_row(d)

		self.set_skip_delivery_note_for_order()

	def set_skip_delivery_note_for_row(self, row, update=False, update_modified=True):
		if row.item_code:
			item = frappe.get_cached_doc("Item", row.item_code)
			row.skip_delivery_note = get_skip_delivery_note(item, delivered_by_supplier=cint(row.delivered_by_supplier))
			if not row.skip_delivery_note:
				hooked_skip_delivery_note = self.run_method("get_skip_delivery_note", row)
				if hooked_skip_delivery_note is not None:
					row.skip_delivery_note = 1 if hooked_skip_delivery_note else 0
				else:
					row.skip_delivery_note = 0
		else:
			row.skip_delivery_note = 1

		if update:
			row.db_set("skip_delivery_note", row.skip_delivery_note, update_modified=update_modified)

	def get_skip_delivery_note(self, row):
		return None

	def set_skip_delivery_note_for_order(self, update=False, update_modified=True):
		all_skip_delivery_note = all(d.skip_delivery_note for d in self.get("items"))
		self.skip_delivery_note = cint(all_skip_delivery_note)

		if update:
			self.db_set("skip_delivery_note", self.skip_delivery_note, update_modified=update_modified)

	def postprocess_after_mapping(self, reset_taxes=False):
		self.set_missing_values()

		if reset_taxes:
			self.reset_taxes_and_charges()
		else:
			self.set_taxes_and_charges()

		self.calculate_taxes_and_totals()
		self.set_payment_schedule()

	def set_advance_paid_amount(self, update=False, update_modified=True):
		from erpnext.accounts.utils import get_balance_on_voucher

		if self.docstatus != 0:
			party_type, party, party_name = self.get_billing_party()
			self.advance_paid = get_balance_on_voucher(self.doctype, self.name, party_type, party,
				account=None, company=self.company,
				dr_or_cr="credit_in_account_currency - debit_in_account_currency",
				include_original_references=True
			)
		else:
			self.advance_paid = 0

		if update:
			self.db_set("advance_paid", self.advance_paid, update_modified=update_modified)

	def validate_with_previous_doc(self):
		super(SalesOrder, self).validate_with_previous_doc({
			"Quotation": {
				"ref_dn_field": "quotation",
				"compare_fields": [["company", "="], ["branch", "="], ["order_type", "="]]
			}
		})

	def validate_previous_docstatus(self):
		pass

	def update_previous_doc_status(self):
		for quotation in list(set([d.quotation for d in self.get("items")])):
			if quotation:
				doc = frappe.get_doc("Quotation", quotation)
				if doc.docstatus == 2:
					frappe.throw(_("Quotation {0} is cancelled").format(quotation))

				doc.set_status(update=True)
				doc.set_ordered_status(update=True)
				doc.update_opportunity()
				doc.notify_update()

		self.update_project_billing_and_sales()

	def set_delivery_status(self, update=False, update_modified=True):
		data = self.get_delivery_status_data()

		# update values in rows
		for d in self.items:
			d.delivered_qty = flt(data.delivered_qty_map.get(d.name))
			if not d.delivered_qty:
				d.delivered_qty = flt(data.service_billed_qty_map.get(d.name))

			d.total_returned_qty = flt(data.total_returned_qty_map.get(d.name))

			if update:
				d.db_set({
					'delivered_qty': d.delivered_qty,
					'total_returned_qty': d.total_returned_qty,
				}, update_modified=update_modified)

		# update percentage in parent
		self.per_delivered, within_allowance = self.calculate_status_percentage('delivered_qty', 'qty', data.deliverable_rows,
			under_delivery_allowance=True)
		if self.per_delivered is None:
			self.per_delivered, within_allowance = self.calculate_status_percentage('delivered_qty', 'qty', self.items,
				under_delivery_allowance=True)
			self.per_delivered = flt(self.per_delivered)

		# update delivery_status
		self.delivery_status = self.get_completion_status('per_delivered', 'Deliver',
			not_applicable=self.skip_delivery_note or self.status == "Closed", within_allowance=within_allowance)

		if update:
			self.db_set({
				'per_delivered': self.per_delivered,
				'delivery_status': self.delivery_status,
			}, update_modified=update_modified)

	def set_production_packing_status(self, update=False, update_modified=True):
		data = self.get_production_packing_status_data()

		# update values in rows
		for d in self.items:
			d.work_order_qty = flt(data.work_order_qty_map.get(d.name))
			d.produced_qty = flt(data.produced_qty_map.get(d.name))
			d.packed_qty = flt(data.packed_qty_map.get(d.name))

			if update:
				d.db_set({
					'work_order_qty': d.work_order_qty,
					'produced_qty': d.produced_qty,
					'packed_qty': d.packed_qty,
				}, update_modified=update_modified)

		# update percentage in parent
		self.per_packed, within_allowance = self.calculate_status_percentage('packed_qty', 'qty', data.producible_rows,
			under_delivery_allowance=True)
		if self.per_packed is None:
			self.per_packed, within_allowance = self.calculate_status_percentage('packed_qty', 'qty', self.items,
				under_delivery_allowance=True)
			self.per_packed = flt(self.per_packed)

		# update packing_status
		self.packing_status = self.get_completion_status('per_packed', 'Pack',
			not_applicable=self.skip_delivery_note or self.status == "Closed",
			within_allowance=within_allowance and not data.has_unpacked_work_orders)

		if update:
			self.db_set({
				'per_packed': self.per_packed,
				'packing_status': self.packing_status,
			}, update_modified=update_modified)

	def set_billing_status(self, update=False, update_modified=True):
		data = self.get_billing_status_data()

		# update values in rows
		for d in self.items:
			d.billed_qty = flt(data.billed_qty_map.get(d.name))
			d.billed_amt = flt(data.billed_amount_map.get(d.name))
			d.returned_qty = flt(data.delivery_return_qty_map.get(d.name))
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

		# update billing_status
		deliveries_not_billable = self.delivery_status == "Delivered" and not data.has_unbilled_delivery
		undeliverable_rows_billed = all(d.billed_qty >= d.qty for d in data.undeliverable_rows)
		not_billable = deliveries_not_billable and undeliverable_rows_billed
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

	def set_purchase_status(self, update=False, update_modified=True):
		purchase_order_qty_map = self.get_purchase_order_qty_map()

		# update values in rows
		for d in self.items:
			d.ordered_qty = flt(purchase_order_qty_map.get(d.name))

			if update:
				d.db_set({
					'ordered_qty': d.ordered_qty,
				}, update_modified=update_modified)

	def get_delivery_status_data(self):
		out = frappe._dict()

		out.deliverable_rows = []
		out.delivered_qty_map = {}
		out.total_returned_qty_map = {}
		out.service_billed_qty_map = {}

		delivery_by_supplier_row_names = []
		delivery_by_stock_row_names = []
		delivery_by_billing_row_names = []

		for d in self.items:
			is_deliverable = not d.skip_delivery_note or d.delivered_by_supplier
			if is_deliverable:
				out.deliverable_rows.append(d)

				if d.delivered_by_supplier:
					delivery_by_supplier_row_names.append(d.name)
				else:
					delivery_by_stock_row_names.append(d.name)
			else:
				delivery_by_billing_row_names.append(d.name)

		# Get Delivered Qty
		if self.docstatus == 1:
			if delivery_by_stock_row_names:
				# Delivered By Delivery Note
				delivered_by_dn = frappe.db.sql("""
					select i.sales_order_item, i.qty, p.is_return, p.reopen_order
					from `tabDelivery Note Item` i
					inner join `tabDelivery Note` p on p.name = i.parent
					where p.docstatus = 1 and i.sales_order_item in %s
				""", [delivery_by_stock_row_names], as_dict=1)

				for d in delivered_by_dn:
					if not d.is_return or d.reopen_order:
						out.delivered_qty_map.setdefault(d.sales_order_item, 0)
						out.delivered_qty_map[d.sales_order_item] += d.qty

					if d.is_return:
						out.total_returned_qty_map.setdefault(d.sales_order_item, 0)
						out.total_returned_qty_map[d.sales_order_item] -= d.qty

				# Delivered By Sales Invoice
				delivered_by_sinv = frappe.db.sql("""
					select i.sales_order_item, i.qty, p.is_return, p.reopen_order
					from `tabSales Invoice Item` i
					inner join `tabSales Invoice` p on p.name = i.parent
					where p.docstatus = 1 and p.update_stock = 1 and i.sales_order_item in %s
				""", [delivery_by_stock_row_names], as_dict=1)

				for d in delivered_by_sinv:
					if not d.is_return or d.reopen_order:
						out.delivered_qty_map.setdefault(d.sales_order_item, 0)
						out.delivered_qty_map[d.sales_order_item] += d.qty

					if d.is_return:
						out.total_returned_qty_map.setdefault(d.sales_order_item, 0)
						out.total_returned_qty_map[d.sales_order_item] -= d.qty

			if delivery_by_supplier_row_names:
				# Delivered By Purchase Order
				delivered_by_po = frappe.db.sql("""
					select i.sales_order_item, i.qty
					from `tabPurchase Order Item` i
					inner join `tabPurchase Order` p on p.name = i.parent
					where p.docstatus = 1 and p.status = 'Delivered'
						and i.sales_order_item in %s
				""", [delivery_by_supplier_row_names], as_dict=1)

				for d in delivered_by_po:
					out.delivered_qty_map.setdefault(d.sales_order_item, 0)
					out.delivered_qty_map[d.sales_order_item] += d.qty

			# Get Service Items Billed Qty as Delivered Qty
			if delivery_by_billing_row_names:
				out.service_billed_qty_map = dict(frappe.db.sql("""
					select i.sales_order_item, sum(i.qty)
					from `tabSales Invoice Item` i
					inner join `tabSales Invoice` p on p.name = i.parent
					where p.docstatus = 1 and (p.is_return = 0 or p.reopen_order = 1)
						and i.sales_order_item in %s
					group by i.sales_order_item
				""", [delivery_by_billing_row_names]))

		return out

	def get_production_packing_status_data(self):
		out = frappe._dict()

		out.producible_rows = []
		out.work_order_qty_map = {}
		out.produced_qty_map = {}
		out.packed_qty_map = {}
		out.has_unpacked_work_orders = False

		producible_row_names = []

		for d in self.items:
			if d.is_stock_item:
				out.producible_rows.append(d)
				producible_row_names.append(d.name)

		if self.docstatus == 1:
			if producible_row_names:
				# Work Order data
				work_order_data = frappe.db.sql("""
					select sales_order_item, qty, completed_qty, packing_status, packing_slip_required
					from `tabWork Order`
					where docstatus = 1 and sales_order_item in %s
				""", [producible_row_names], as_dict=1)

				for d in work_order_data:
					out.work_order_qty_map.setdefault(d.sales_order_item, 0)
					out.work_order_qty_map[d.sales_order_item] += d.qty

					out.produced_qty_map.setdefault(d.sales_order_item, 0)
					out.produced_qty_map[d.sales_order_item] += d.completed_qty

					if d.packing_status == "To Pack":
						out.has_unpacked_work_orders = True

				# Packed by Packing Slip
				packed_by_packing_slip = frappe.db.sql("""
					select i.sales_order_item, i.qty - i.unpacked_return_qty as qty
					from `tabPacking Slip Item` i
					inner join `tabPacking Slip` p on p.name = i.parent
					where p.docstatus = 1 and i.sales_order_item in %s and ifnull(i.source_packing_slip, '') = ''
				""", [producible_row_names], as_dict=1)

				for d in packed_by_packing_slip:
					out.packed_qty_map.setdefault(d.sales_order_item, 0)
					out.packed_qty_map[d.sales_order_item] += d.qty

		return out

	def get_billing_status_data(self):
		out = frappe._dict()
		out.undeliverable_rows = []
		out.billed_qty_map = {}
		out.billed_amount_map = {}
		out.delivery_return_qty_map = {}
		out.depreciation_type_qty = {}
		out.has_unbilled_delivery = False

		for d in self.items:
			is_deliverable = not d.skip_delivery_note or d.delivered_by_supplier
			if not is_deliverable:
				out.undeliverable_rows.append(d)

		if self.docstatus == 1:
			row_names = [d.name for d in self.items]
			if row_names:
				# Billed By Sales Invoice
				billed_by_sinv = frappe.db.sql("""
					select i.sales_order_item, i.qty, i.amount, p.depreciation_type, p.is_return, p.reopen_order,
						p.customer, p.bill_to
					from `tabSales Invoice Item` i
					inner join `tabSales Invoice` p on p.name = i.parent
					where p.docstatus = 1 and (p.is_return = 0 or p.reopen_order = 1)
						and i.sales_order_item in %s
				""", [row_names], as_dict=1)

				for d in billed_by_sinv:
					bill_to = d.bill_to or d.customer
					so_row = self.getone('items', {'name': d.sales_order_item})
					claim_customer = so_row.claim_customer if so_row else None

					out.billed_amount_map.setdefault(d.sales_order_item, 0)
					out.billed_amount_map[d.sales_order_item] += d.amount

					depreciation_type = d.depreciation_type or 'No Depreciation'
					out.depreciation_type_qty.setdefault(d.sales_order_item, {}).setdefault(depreciation_type, 0)
					out.depreciation_type_qty[d.sales_order_item][depreciation_type] += d.qty

					if d.depreciation_type != 'Depreciation Amount Only' and (not claim_customer or bill_to == claim_customer):
						out.billed_qty_map.setdefault(d.sales_order_item, 0)
						out.billed_qty_map[d.sales_order_item] += d.qty

				# Do not mark as billed if both depreciation type invoices not created
				for so_item, depreciation_types in out.depreciation_type_qty.items():
					if 'No Depreciation' not in depreciation_types:
						depreciation_qty = flt(depreciation_types.get('Depreciation Amount Only'), 6)
						after_depreciation_qty = flt(depreciation_types.get('After Depreciation Amount'), 6)
						if not depreciation_qty or not after_depreciation_qty:
							out.billed_qty_map[so_item] = 0

				# Returned By Delivery Note
				delivered_by_dn = frappe.db.sql("""
					select i.sales_order_item, i.qty, p.is_return, p.reopen_order, p.billing_status
					from `tabDelivery Note Item` i
					inner join `tabDelivery Note` p on p.name = i.parent
					where p.docstatus = 1 and i.sales_order_item in %s
				""", [row_names], as_dict=1)

				for d in delivered_by_dn:
					if d.is_return and not d.reopen_order:
						out.delivery_return_qty_map.setdefault(d.sales_order_item, 0)
						out.delivery_return_qty_map[d.sales_order_item] -= d.qty

					if d.billing_status == "To Bill":
						out.has_unbilled_delivery = True

		return out

	def get_purchase_order_qty_map(self):
		purchase_order_qty_map = {}

		if self.docstatus == 1:
			row_names = [d.name for d in self.items]
			if row_names:
				purchase_order_qty_map = dict(frappe.db.sql("""
					select i.sales_order_item, sum(i.qty)
					from `tabPurchase Order Item` i
					inner join `tabPurchase Order` p on p.name = i.parent
					where p.docstatus = 1 and i.sales_order_item in %s
					group by i.sales_order_item
				""", [row_names]))

		return purchase_order_qty_map

	def validate_delivered_qty(self, from_doctype=None, row_names=None, check_packed_qty=False):
		if check_packed_qty:
			self.validate_completed_qty('delivered_qty', 'packed_qty', self.items,
				allowance_type=None, from_doctype=from_doctype, row_names=row_names)
		else:
			self.validate_completed_qty('delivered_qty', 'qty', self.items,
				allowance_type='qty', from_doctype=from_doctype, row_names=row_names)

	def validate_packed_qty(self, from_doctype=None, row_names=None):
		self.validate_completed_qty('packed_qty', 'qty', self.items,
			allowance_type='qty', from_doctype=from_doctype, row_names=row_names)

	def validate_billed_qty(self, from_doctype=None, row_names=None):
		self.validate_completed_qty(['billed_qty', 'returned_qty'], 'qty', self.items,
			allowance_type='billing', from_doctype=from_doctype, row_names=row_names)

		if frappe.get_cached_value("Accounts Settings", None, "validate_over_billing_in_sales_invoice"):
			self.validate_completed_qty('billed_amt', 'amount', self.items,
				allowance_type='billing', from_doctype=from_doctype, row_names=row_names)

	def validate_po(self):
		# validate p.o date v/s delivery date
		if self.po_date and not self.skip_delivery_note:
			for d in self.get("items"):
				if d.delivery_date and getdate(d.delivery_date) < getdate(self.po_date):
					frappe.throw(_("Row #{0}: Expected Delivery Date cannot be before Purchase Order Date")
						.format(d.idx))

		if self.po_no and self.customer:
			so = frappe.db.sql("""
				select name
				from `tabSales Order`
				where po_no = %s and name != %s and docstatus < 2 and customer = %s
			""", (self.po_no, self.name, self.customer))
			so = so[0][0] if so else None

			if so and not cint(frappe.get_cached_value("Selling Settings", None, "allow_against_multiple_purchase_orders")):
				frappe.msgprint(_("Warning: {0} already exists against Customer's Purchase Order {1}").format(
					frappe.get_desk_link("Sales Order", so), frappe.bold(self.po_no)
				))

	def validate_for_items(self):
		for d in self.get('items'):
			# used for production plan
			d.transaction_date = self.transaction_date

			tot_avail_qty = frappe.db.sql("select projected_qty from `tabBin` \
				where item_code = %s and warehouse = %s", (d.item_code, d.warehouse))
			d.projected_qty = tot_avail_qty and flt(tot_avail_qty[0][0]) or 0

	def validate_delivery_date(self):
		if self.skip_delivery_note or self.order_type != "Sales":
			return

		delivery_date_list = [getdate(d.delivery_date) for d in self.get("items") if d.delivery_date]
		max_delivery_date = max(delivery_date_list) if delivery_date_list else None

		if not self.delivery_date:
			self.delivery_date = max_delivery_date

		warned = False
		if self.delivery_date:
			for d in self.get("items"):
				if not d.delivery_date:
					d.delivery_date = self.delivery_date

				if getdate(self.transaction_date) > getdate(d.delivery_date) and not warned:
					frappe.msgprint(_("Expected Delivery Date should be after Sales Order Date"),
						indicator='orange', title=_('Warning'))
					warned = True

			if getdate(self.delivery_date) != getdate(max_delivery_date):
				self.delivery_date = max_delivery_date

	def validate_delivery_date_required(self):
		if self.order_type == 'Sales' and not self.skip_delivery_note:
			if not self.delivery_date:
				frappe.throw(_("Please enter Expected Delivery Date"))

	def validate_warehouse(self):
		super(SalesOrder, self).validate_warehouse()

		for d in self.get("items"):
			if d.get("warehouse"):
				continue

			if d.is_stock_item and not cint(d.skip_delivery_note):
				frappe.throw(_("Row #{0}: Delivery Warehouse required for Stock Item {0}").format(d.idx, d.item_code),
					WarehouseRequired)

	def validate_drop_ship(self):
		for d in self.get('items'):
			if d.delivered_by_supplier and not d.supplier:
				frappe.throw(_("Row #{0}: Set Supplier for item {1}").format(d.idx, d.item_code))

	def check_credit_limit(self):
		# if bypass credit limit check is set to true (1) at sales order level,
		# then we need not to check credit limit and vise versa
		if not cint(frappe.db.get_value("Customer Credit Limit",
			{'parent': self.customer, 'parenttype': 'Customer', 'company': self.company},
			"bypass_credit_limit_check")):
			check_credit_limit(self.customer, self.company)

	def check_nextdoc_docstatus(self):
		# Checks Delivery Note
		submit_dn = frappe.db.sql_list("""
			select t1.name
			from `tabDelivery Note` t1,`tabDelivery Note Item` t2
			where t1.name = t2.parent and t2.sales_order = %s and t1.docstatus = 1""", self.name)

		if submit_dn:
			submit_dn = [get_link_to_form("Delivery Note", dn) for dn in submit_dn]
			frappe.throw(_("Delivery Notes {0} must be cancelled before cancelling this Sales Order")
				.format(", ".join(submit_dn)))

		# Checks Sales Invoice
		submit_rv = frappe.db.sql_list("""select t1.name
			from `tabSales Invoice` t1,`tabSales Invoice Item` t2
			where t1.name = t2.parent and t2.sales_order = %s and t1.docstatus = 1""",
			self.name)

		if submit_rv:
			submit_rv = [get_link_to_form("Sales Invoice", si) for si in submit_rv]
			frappe.throw(_("Sales Invoice {0} must be cancelled before cancelling this Sales Order")
				.format(", ".join(submit_rv)))

		# check work order
		pro_order = frappe.db.sql_list("""
			select name
			from `tabWork Order`
			where sales_order = %s and docstatus = 1""", self.name)

		if pro_order:
			pro_order = [get_link_to_form("Work Order", po) for po in pro_order]
			frappe.throw(_("Work Order {0} must be cancelled before cancelling this Sales Order")
				.format(", ".join(pro_order)))

	def check_modified_date(self):
		mod_db = frappe.db.get_value("Sales Order", self.name, "modified")
		date_diff = frappe.db.sql("select TIMEDIFF('%s', '%s')" %
			( mod_db, cstr(self.modified)))
		if date_diff and date_diff[0][0]:
			frappe.throw(_("{0} {1} has been modified. Please refresh.").format(self.doctype, self.name))

	def update_reserved_qty(self, so_item_rows=None):
		"""update requested qty (before ordered_qty is updated)"""
		def add_to_item_warehouse_list(item_code, warehouse):
			if not item_code or not warehouse:
				return
			if (item_code, warehouse) in item_warehouse_list:
				return
			if frappe.db.get_value("Item", item_code, "is_stock_item", cache=1):
				item_warehouse_list.append((item_code, warehouse))

		item_warehouse_list = []

		for d in self.get("items"):
			if not so_item_rows or d.name in so_item_rows:
				if item_has_product_bundle(d.item_code):
					for p in self.get("packed_items"):
						if p.parent_detail_docname == d.name and p.parent_item == d.item_code:
							add_to_item_warehouse_list(p.item_code, p.warehouse)
				else:
					add_to_item_warehouse_list(d.item_code, d.warehouse)

		for item_code, warehouse in item_warehouse_list:
			update_bin_qty(item_code, warehouse, {
				"reserved_qty": get_reserved_qty(item_code, warehouse)
			})

	def validate_supplier_after_submit(self):
		"""Check that supplier is the same after submit if PO is already made"""
		exc_list = []

		for item in self.items:
			if item.supplier:
				supplier = frappe.db.get_value("Sales Order Item", {"parent": self.name, "item_code": item.item_code},
					"supplier")
				if item.ordered_qty > 0.0 and item.supplier != supplier:
					exc_list.append(_("Row #{0}: Not allowed to change Supplier as Purchase Order already exists").format(item.idx))

		if exc_list:
			frappe.throw('\n'.join(exc_list))

	@frappe.whitelist()
	def get_work_order_items(self, for_raw_material_request=False, item_condition=None):
		"""Returns items with BOM that already do not have a linked work order"""
		work_order_items = []
		item_codes = [i.item_code for i in self.items]
		product_bundle_parents = frappe.get_all("Product Bundle", {"new_item_code": ["in", item_codes]}, pluck="new_item_code")
		default_rm_warehouse = frappe.get_cached_value("Manufacturing Settings", None, "default_rm_warehouse")
		for_raw_material_request = cint(for_raw_material_request)

		for d in self.get("items") + self.get("packed_items", []):
			if item_condition and not item_condition(d):
				continue

			bom_no = self.run_method("get_sales_order_item_bom", d)
			if not bom_no:
				bom_no = get_default_bom(d.item_code)

			if not bom_no:
				continue

			stock_qty = flt(d.qty) if d.doctype == "Packed Item" else flt(d.stock_qty)
			if for_raw_material_request:
				pending_qty = stock_qty
			else:
				work_order_data = frappe.db.sql("""
					select sum(qty)
					from `tabWork Order`
					where production_item = %s and sales_order = %s and sales_order_item = %s and docstatus < 2
				""", (d.item_code, self.name, d.name))
				total_work_order_qty = flt(work_order_data[0][0]) if work_order_data else 0
				pending_qty = stock_qty - total_work_order_qty

			work_order_precison = frappe.get_precision("Work Order", "qty")
			pending_qty = round_up(pending_qty, work_order_precison)

			if pending_qty and d.item_code not in product_bundle_parents:
				wo_item = {
					"name": d.name,
					"item_code": d.item_code,
					"item_name": d.item_name,
					"description": d.description,
					"bom_no": bom_no,
					"warehouse": default_rm_warehouse if for_raw_material_request else d.warehouse,
					"stock_uom": d.get("stock_uom") or d.get("uom"),
					"sales_order": self.name,
					"sales_order_item": d.name,
					"order_line_no": d.idx,
					"project": self.project,
					"cost_center": self.get("cost_center") or d.get("cost_center"),
					"delivery_date": d.delivery_date,
				}

				if for_raw_material_request:
					wo_item["required_qty"] = pending_qty
				else:
					wo_item["pending_qty"] = pending_qty
					wo_item["production_qty"] = pending_qty

				work_order_items.append(wo_item)

		return work_order_items

	def get_sales_order_item_bom(self, row):
		return None

	def on_recurring(self, reference_doc, auto_repeat_doc):
		def _get_delivery_date(ref_doc_delivery_date, red_doc_transaction_date, transaction_date):
			delivery_date = auto_repeat_doc.get_next_schedule_date(schedule_date=ref_doc_delivery_date)

			if delivery_date <= transaction_date:
				delivery_date_diff = frappe.utils.date_diff(ref_doc_delivery_date, red_doc_transaction_date)
				delivery_date = frappe.utils.add_days(transaction_date, delivery_date_diff)

			return delivery_date

		self.set("delivery_date", _get_delivery_date(reference_doc.delivery_date,
			reference_doc.transaction_date, self.transaction_date))

		for d in self.get("items"):
			reference_delivery_date = frappe.db.get_value("Sales Order Item",
				{"parent": reference_doc.name, "item_code": d.item_code, "idx": d.idx}, "delivery_date")

			d.set("delivery_date", _get_delivery_date(reference_delivery_date,
				reference_doc.transaction_date, self.transaction_date))

	def validate_serial_no_based_delivery(self):
		reserved_items = []
		normal_items = []
		for item in self.items:
			if item.ensure_delivery_based_on_produced_serial_no:
				if item.item_code in normal_items:
					frappe.throw(_("Cannot ensure delivery by Serial No as \
					Item {0} is added with and without Ensure Delivery by \
					Serial No.").format(item.item_code))
				if item.item_code not in reserved_items:
					if not frappe.get_cached_value("Item", item.item_code, "has_serial_no"):
						frappe.throw(_("Item {0} has no Serial No. Only serilialized items \
						can have delivery based on Serial No").format(item.item_code))
					if not frappe.db.exists("BOM", {"item": item.item_code, "is_active": 1}):
						frappe.throw(_("No active BOM found for item {0}. Delivery by \
						Serial No cannot be ensured").format(item.item_code))
				reserved_items.append(item.item_code)
			else:
				normal_items.append(item.item_code)

			if not item.ensure_delivery_based_on_produced_serial_no and \
				item.item_code in reserved_items:
				frappe.throw(_("Cannot ensure delivery by Serial No as \
				Item {0} is added with and without Ensure Delivery by \
				Serial No.").format(item.item_code))


@frappe.whitelist()
def update_status(status, name):
	if not frappe.has_permission("Sales Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	so = frappe.get_doc("Sales Order", name)
	so.run_method("update_status", status)

	frappe.msgprint(_("{0} is {1}").format(frappe.get_desk_link("Sales Order", name), status))


@frappe.whitelist()
def close_or_unclose_sales_orders(names, status):
	if not frappe.has_permission("Sales Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	names = json.loads(names)
	for name in names:
		so = frappe.get_doc("Sales Order", name)
		if so.docstatus == 1:
			if status == "Closed":
				if so.status not in ("Cancelled", "Closed") and (so.delivery_status == "To Deliver" or so.billing_status == "To Bill"):
					so.run_method("update_status", status)
			else:
				if so.status == "Closed":
					so.run_method("update_status", "Draft")
			so.update_blanket_order()

	frappe.local.message_log = []


def get_requested_item_qty(sales_order):
	return frappe._dict(frappe.db.sql("""
		select sales_order_item, sum(stock_qty)
		from `tabMaterial Request Item`
		where docstatus = 1
			and sales_order = %s
		group by sales_order_item
	""", sales_order))


@frappe.whitelist()
def make_material_request(source_name, target_doc=None):
	requested_item_qty_map = get_requested_item_qty(source_name)

	def get_pending_qty(source):
		qty = flt(source.get("qty"))
		return qty - flt(requested_item_qty_map.get(source.name))

	def item_condition(source, source_parent, target_parent):
		if source.name in [d.sales_order_item for d in target_parent.get('items') if d.sales_order_item]:
			return False

		if source.skip_delivery_note:
			return False

		if frappe.db.exists('Product Bundle', source.item_code):
			return False

		return flt(source.stock_qty) > flt(requested_item_qty_map.get(source.name))

	def update_item(source, target, source_parent, target_parent):
		# qty is for packed items, because packed items don't have stock_qty field
		target.qty = get_pending_qty(source)
		target.project = source_parent.project

	def postprocess(source, target):
		target.run_method("postprocess_after_mapping")

	doc = get_mapped_doc("Sales Order", source_name, {
		"Sales Order": {
			"doctype": "Material Request",
			"validation": {
				"docstatus": ["=", 1]
			}
		},
		"Packed Item": {
			"doctype": "Material Request Item",
			"field_map": {
				"parent": "sales_order",
				"uom": "uom"
			},
			"postprocess": update_item
		},
		"Sales Order Item": {
			"doctype": "Material Request Item",
			"field_map": {
				"name": "sales_order_item",
				"parent": "sales_order"
			},
			"condition": item_condition,
			"postprocess": update_item
		}
	}, target_doc, postprocess)

	return doc


@frappe.whitelist()
def make_purchase_invoice(supplier, source_name, target_doc=None):
	def set_missing_values(source, target):
		target.supplier = supplier
		target.apply_discount_on = ""
		target.additional_discount_percentage = 0.0
		target.discount_amount = 0.0
		target.update_stock = 1

		target.tax_id, target.tax_cnic, target.tax_strn = frappe.get_value("Supplier", supplier, ['tax_id', 'tax_cnic', 'tax_strn'])

		if target.get('taxes_and_charges'): target.taxes_and_charges = ""
		if target.get('taxes'): target.taxes = []
		default_tax = get_default_taxes_and_charges("Purchase Taxes and Charges Template", company=target.company)
		target.update(default_tax)

		default_price_list = frappe.get_value("Supplier", supplier, "default_price_list")
		if default_price_list:
			target.buying_price_list = default_price_list

		if target.get('payment_terms_template'): target.payment_terms_template = ""
		if target.get('address_display'): target.address_display = ""
		if target.get('shipping_address'): target.shipping_address = ""

		target.run_method("postprocess_after_mapping")

	def update_item(source, target, source_parent, target_parent):
		target.discount_percentage = 0
		target.price_list_rate = 0
		target.rate = 0
		target.qty = flt(source.qty) - flt(source.ordered_qty)
		target.project = source_parent.project

	doc = get_mapped_doc("Sales Order", source_name, {
		"Sales Order": {
			"doctype": "Purchase Invoice",
			"validation": {
				"docstatus": ["=", 1]
			}
		},
		"Sales Order Item": {
			"doctype": "Purchase Invoice Item",
			"postprocess": update_item
		}
	}, target_doc, set_missing_values)

	return doc


@frappe.whitelist()
def make_project(source_name, target_doc=None):
	def postprocess(source, target):
		service_templates = []
		for d in source.items:
			if d.service_template and d.service_template not in service_templates:
				service_templates.append(d.service_template)

		for service_template in service_templates:
			pt_row = target.append("service_templates")
			pt_row.service_template = service_template
			pt_row.sales_order = source.name

		target.run_method("set_missing_values")

	doc = get_mapped_doc("Sales Order", source_name, {
		"Sales Order": {
			"doctype": "Project",
			"validation": {
				"docstatus": ["=", 1]
			},
			"field_map": {
				"name": "sales_order",
				"delivery_date": "expected_delivery_date",
			},
		},
	}, target_doc, postprocess)

	return doc


@frappe.whitelist()
def make_delivery_note(source_name, target_doc=None, warehouse=None, skip_item_mapping=False, allow_duplicate=False):
	if not warehouse and frappe.flags.args:
		warehouse = frappe.flags.args.warehouse

	def set_missing_values(source, target):
		target.ignore_pricing_rule = 1

		if not skip_item_mapping:
			update_mapped_items_based_on_purchase_and_production(source, target)
			split_vehicle_items_by_qty(target)
			set_reserved_vehicles_from_so(source, target)

		if warehouse:
			target.set_warehouse = warehouse

		target.run_method("postprocess_after_mapping")

	mapper = {
		"Sales Order": {
			"doctype": "Delivery Note",
			"validation": {
				"docstatus": ["=", 1]
			},
			"field_map": {
				"remarks": "remarks"
			}
		},
		"Sales Taxes and Charges": {
			"doctype": "Sales Taxes and Charges",
			"add_if_empty": True
		},
		"Sales Team": {
			"doctype": "Sales Team",
			"add_if_empty": True
		},
		"postprocess": set_missing_values,
	}

	if not skip_item_mapping:
		mapper["Sales Order Item"] = get_item_mapper_for_delivery(allow_duplicate=allow_duplicate)

	frappe.utils.call_hook_method("update_delivery_note_from_sales_order_mapper", mapper, "Delivery Note")

	target_doc = get_mapped_doc("Sales Order", source_name, mapper, target_doc)

	return target_doc


@frappe.whitelist()
def make_delivery_note_from_packing_slips(source_name, target_doc=None, packing_filter=None, warehouse=None):
	from erpnext.controllers.queries import _get_packing_slips_to_be_delivered

	from erpnext.stock.doctype.packing_slip.packing_slip import make_delivery_note as map_dn_from_packing_slip
	if not warehouse and frappe.flags.args:
		warehouse = frappe.flags.args.warehouse
	if not packing_filter and frappe.flags.args:
		packing_filter = frappe.flags.args.packing_filter

	packing_slip_filters = {"sales_order": source_name}
	if frappe.flags.selected_children and frappe.flags.selected_children.get("items"):
		packing_slip_filters["sales_order_item"] = frappe.flags.selected_children["items"]

	packing_slips = _get_packing_slips_to_be_delivered(filters=packing_slip_filters)
	for d in packing_slips:
		target_doc = map_dn_from_packing_slip(d.name, target_doc)

	if packing_filter != "Packed Items Only":
		target_doc = make_delivery_note(source_name, target_doc, warehouse=warehouse, allow_duplicate=True)

	return target_doc


def get_item_mapper_for_delivery(allow_duplicate=False):
	def update_item(source, target, source_parent, target_parent):
		undelivered_qty, unpacked_qty = get_remaining_qty(source)
		target.qty = min(undelivered_qty, unpacked_qty)

	def item_condition(source, source_parent, target_parent):
		if not allow_duplicate:
			if source.name in [d.sales_order_item for d in target_parent.get('items') if d.sales_order_item]:
				return False

		if source.skip_delivery_note:
			return False

		undelivered_qty, unpacked_qty = get_remaining_qty(source)
		return undelivered_qty > 0 and unpacked_qty > 0

	def get_remaining_qty(source):
		undelivered_qty = flt(source.qty) - flt(source.delivered_qty)
		unpacked_qty = flt(source.qty) - flt(source.packed_qty)

		return undelivered_qty, unpacked_qty

	return {
		"doctype": "Delivery Note Item",
		"field_map": {
			"rate": "rate",
			"name": "sales_order_item",
			"parent": "sales_order",
			"quotation": "quotation",
			"quotation_item": "quotation_item",
		},
		"postprocess": update_item,
		"condition": item_condition,
	}


def update_mapped_items_based_on_purchase_and_production(source, target):
	updated_rows = []

	for target_item in target.get("items"):
		updated_rows.append(target_item)

		item = frappe.get_cached_value("Item", target_item.item_code,
			['has_batch_no', 'has_serial_no'], as_dict=1)
		if not item:
			continue

		if target_item.sales_order and (item.has_batch_no or item.has_serial_no) and not target_item.get("packing_slip"):
			purchase_receipt_items = frappe.db.sql("""
				select i.batch_no, i.serial_no, i.qty
				from `tabPurchase Receipt Item` i
				inner join `tabPurchase Receipt` prec on prec.name = i.parent
				inner join `tabPurchase Order Item` po_item on po_item.name = i.purchase_order_item
				where i.docstatus = 1 and (prec.is_return = 0 or prec.reopen_order = 1)
					and po_item.sales_order_item = %s
			""", target_item.sales_order_item, as_dict=1)

			purchase_invoice_items = frappe.db.sql("""
				select i.batch_no, i.serial_no, i.qty
				from `tabPurchase Invoice Item` i
				inner join `tabPurchase Invoice` pinv on pinv.name = i.parent
				inner join `tabPurchase Order Item` po_item on po_item.name = i.purchase_order_item
				where pinv.docstatus = 1 and pinv.update_stock = 1 and (pinv.is_return = 0 or pinv.reopen_order = 1)
					and po_item.sales_order_item = %s
			""", target_item.sales_order_item, as_dict=1)

			produced_items = frappe.db.sql("""
				select i.batch_no, i.serial_no, i.stock_qty / %s as qty
				from `tabStock Entry Detail` i
				inner join `tabStock Entry` ste on ste.name = i.parent
				inner join `tabWork Order` wo on wo.name = ste.work_order
				where ste.docstatus = 1
					and ste.purpose = 'Manufacture'
					and ifnull(i.s_warehouse, '') = ''
					and ifnull(i.t_warehouse, '') != ''
					and wo.sales_order_item = %s
					and i.item_code = %s
			""", (target_item.conversion_factor, target_item.sales_order_item, target_item.item_code), as_dict=1)

			unpacked_delivery_condition = ""
			if target.doctype == "Packing Slip":
				unpacked_delivery_condition = " and ifnull(i.packing_slip, '') = ''"

			delivery_note_items = frappe.db.sql("""
				select i.batch_no, i.serial_no, i.qty
				from `tabDelivery Note Item` i
				inner join `tabDelivery Note` dn on dn.name = i.parent
				where i.docstatus = 1 and (dn.is_return = 0 or dn.reopen_order = 1)
					and i.sales_order_item = %s {0}
			""".format(unpacked_delivery_condition), target_item.sales_order_item, as_dict=1)

			sales_invoice_items = frappe.db.sql("""
				select i.batch_no, i.serial_no, i.qty
				from `tabSales Invoice Item` i
				inner join `tabSales Invoice` sinv on sinv.name = i.parent
				where sinv.docstatus = 1 and sinv.update_stock = 1 and (sinv.is_return = 0 or sinv.reopen_order = 1)
					and i.sales_order_item = %s {0}
			""".format(unpacked_delivery_condition), target_item.sales_order_item, as_dict=1)

			packed_items = []
			if target.doctype == "Packing Slip":
				packed_items = frappe.db.sql("""
					select i.batch_no, i.serial_no, i.qty
					from `tabPacking Slip Item` i
					inner join `tabPacking Slip` ps on ps.name = i.parent
					where i.docstatus = 1 and ifnull(i.source_packing_slip, '') = ''
						and i.sales_order_item = %s
				""", target_item.sales_order_item, as_dict=1)

			incoming_items = (
				[d for d in purchase_receipt_items if d.qty > 0]
				+ [d for d in purchase_invoice_items if d.qty > 0]
				+ [d for d in delivery_note_items if d.qty < 0]
				+ [d for d in sales_invoice_items if d.qty < 0]
				+ [d for d in packed_items if d.qty < 0]
				+ produced_items
			)

			outgoing_items = (
				[d for d in delivery_note_items if d.qty > 0]
				+ [d for d in sales_invoice_items if d.qty > 0]
				+ [d for d in purchase_receipt_items if d.qty < 0]
				+ [d for d in purchase_invoice_items if d.qty < 0]
				+ [d for d in packed_items if d.qty > 0]
			)

			batch_wise_details = {}

			# Get received batch/serial details
			for in_item in incoming_items:
				current_batch = batch_wise_details.setdefault(cstr(in_item.batch_no), frappe._dict({
					"batch_no": in_item.batch_no, "serial_nos": [], "remaining_qty": 0
				}))
				current_batch.remaining_qty += abs(flt(in_item.qty))
				current_batch.serial_nos += get_serial_nos(in_item.serial_no)

			# Remove batch/serial nos delivered
			for out_item in outgoing_items:
				current_batch = batch_wise_details.get(cstr(out_item.batch_no))
				if current_batch:
					current_batch.remaining_qty -= abs(flt(out_item.qty))
					if flt(current_batch.remaining_qty, target_item.precision("qty")) <= 0:
						del batch_wise_details[out_item.batch_no]
						continue

					serial_nos_to_remove = get_serial_nos(out_item.serial_no)
					current_batch.serial_nos = list(filter(lambda d: d and d not in serial_nos_to_remove, current_batch.serial_nos))

			if batch_wise_details:
				batches = list(batch_wise_details.values())
				rows = [target_item]
				for i in range(1, len(batches)):
					new_row = frappe.copy_doc(target_item)
					rows.append(new_row)
					updated_rows.append(new_row)

				for row, batch in zip(rows, batches):
					row.qty = batch.remaining_qty
					row.batch_no = batch.batch_no
					row.serial_no = "\n".join(batch.serial_nos)

	# Replace with updated list
	for i, row in enumerate(updated_rows):
		row.idx = i + 1

	target.items = updated_rows


@frappe.whitelist()
def make_packing_slip(source_name, target_doc=None, warehouse=None):
	if not warehouse and frappe.flags.args:
		warehouse = frappe.flags.args.warehouse

	work_order_cache = {}

	def item_condition(source, source_parent, target_parent):
		if source.name in [d.sales_order_item for d in target_parent.get('items') if d.sales_order_item]:
			return False

		if not source.is_stock_item or source.skip_delivery_note:
			return False

		undelivered_qty, unpacked_qty = get_remaining_qty(source)
		return undelivered_qty > 0 and unpacked_qty > 0

	def postprocess(source, target):
		# Target Warehouse
		if not target.target_warehouse:
			if warehouse:
				target.target_warehouse = warehouse
			else:
				target.determine_warehouse_from_sales_order()

		update_mapped_items_based_on_purchase_and_production(source, target)

		frappe.utils.call_hook_method("postprocess_sales_order_to_packing_slip", source, target)

		target.run_method("postprocess_after_mapping")

	def update_item(source, target, source_parent, target_parent):
		work_order_details = get_work_order_details(source)
		target.work_order = work_order_details.name if work_order_details else None

		# Source Warehouse
		if work_order_details:
			if work_order_details.produce_fg_in_wip_warehouse:
				target.source_warehouse = work_order_details.wip_warehouse
			else:
				target.source_warehouse = work_order_details.fg_warehouse
		else:
			target.source_warehouse = source.warehouse

		# Remaining Qty
		undelivered_qty, unpacked_qty = get_remaining_qty(source)
		target.qty = min(undelivered_qty, unpacked_qty)

	def get_remaining_qty(source):
		work_order_details = get_work_order_details(source)
		work_order = work_order_details.name if work_order_details else None

		if work_order:
			packable_qty = flt(work_order_details.completed_qty) - flt(work_order_details.rejected_qty) - flt(work_order_details.reconciled_qty)
			packable_qty_order_uom = packable_qty / source.conversion_factor

			undelivered_qty = round_down(packable_qty_order_uom - flt(source.delivered_qty), source.precision("qty"))
			unpacked_qty = round_down(packable_qty_order_uom - flt(source.packed_qty), source.precision("qty"))
		else:
			undelivered_qty = flt(source.qty) - flt(source.delivered_qty)
			unpacked_qty = flt(source.qty) - flt(source.packed_qty)

		return undelivered_qty, unpacked_qty

	def get_work_order_details(source):
		if source.name not in work_order_cache:
			work_order_cache[source.name] = frappe.db.get_value("Work Order", filters={
				"sales_order": source.parent,
				"sales_order_item": source.name,
				"docstatus": 1,
				"packing_slip_required": 1,
			}, fieldname=[
				"name", "completed_qty", "rejected_qty", "reconciled_qty",
				"fg_warehouse", "wip_warehouse", "produce_fg_in_wip_warehouse",
			], as_dict=1)

		return work_order_cache[source.name]

	mapper = {
		"Sales Order": {
			"doctype": "Packing Slip",
			"validation": {
				"docstatus": ["=", 1]
			},
			"field_no_map": [
				"total_net_weight",
				"total_gross_weight",
				"remarks",
			]
		},
		"Sales Order Item": {
			"doctype": "Packing Slip Item",
			"field_map": {
				"name": "sales_order_item",
				"parent": "sales_order",
			},
			"field_no_map": [
				"net_weight_per_unit",
				"tare_weight_per_unit",
				"gross_weight_per_unit",
			],
			"postprocess": update_item,
			"condition": item_condition,
		},
		"postprocess": postprocess,
	}

	frappe.utils.call_hook_method("update_packing_slip_from_sales_order_mapper", mapper, "Packing Slip")

	target_doc = get_mapped_doc("Sales Order", source_name, mapper, target_doc)

	return target_doc


@frappe.whitelist()
def make_sales_invoice(source_name, target_doc=None, ignore_permissions=False,
		only_items=None, skip_item_mapping=False, skip_postprocess=False):

	if frappe.flags.args and only_items is None:
		only_items = cint(frappe.flags.args.only_items)

	def postprocess(source, target):
		if not skip_item_mapping:
			split_vehicle_items_by_qty(target)
			set_reserved_vehicles_from_so(source, target)

		target.ignore_pricing_rule = 1
		target.flags.ignore_permissions = ignore_permissions
		target.run_method("postprocess_after_mapping", reset_taxes=True)

		# set the redeem loyalty points if provided via shopping cart
		if source.loyalty_points and source.order_type == "Shopping Cart":
			target.redeem_loyalty_points = 1

		if target.get("allocate_advances_automatically"):
			target.set_advances()

	mapping = {
		"Sales Order": {
			"doctype": "Sales Invoice",
			"field_map": {
				"party_account_currency": "party_account_currency",
				"payment_terms_template": "payment_terms_template",
				"remarks": "remarks",
			},
			"field_no_map": [
				"has_stin",
			],
			"validation": {
				"docstatus": ["=", 1]
			}
		},
		"Sales Taxes and Charges": {
			"doctype": "Sales Taxes and Charges",
			"add_if_empty": True
		},
		"Sales Team": {
			"doctype": "Sales Team",
			"add_if_empty": True
		},
		"postprocess": postprocess if not skip_postprocess else None,
	}

	if not skip_item_mapping:
		mapping["Sales Order Item"] = get_item_mapper_for_invoice(source_name)

	if only_items:
		mapping = {dt: dt_mapping for dt, dt_mapping in mapping.items() if dt == "Sales Order Item"}

	frappe.utils.call_hook_method("update_sales_invoice_from_sales_order_mapper", mapping, "Sales Invoice")

	doclist = get_mapped_doc(
		"Sales Order",
		source_name,
		mapping,
		target_doc=target_doc,
		ignore_permissions=ignore_permissions,
		explicit_child_tables=only_items)

	return doclist


def get_item_mapper_for_invoice(sales_order, allow_duplicate=False):
	unbilled_dn_qty_map = get_unbilled_dn_qty_map(sales_order)

	def get_pending_qty(source):
		billable_qty = flt(source.qty) - flt(source.billed_qty) - flt(source.returned_qty)
		unbilled_dn_qty = flt(unbilled_dn_qty_map.get(source.name))
		return max(billable_qty - unbilled_dn_qty, 0)

	def item_condition(source, source_parent, target_parent):
		if not allow_duplicate:
			if source.name in [d.sales_order_item for d in target_parent.get('items') if d.sales_order_item and not d.delivery_note_item]:
				return False

		if cint(target_parent.get('claim_billing')):
			bill_to = target_parent.get('bill_to') or target_parent.get('customer')
			if bill_to:
				if source.claim_customer != bill_to:
					return False
			else:
				if not source.claim_customer:
					return False

		return get_pending_qty(source)

	def update_item(source, target, source_parent, target_parent):
		target.project = source_parent.get('project')
		target.qty = get_pending_qty(source)
		target.depreciation_percentage = None

		if target_parent:
			target_parent.adjust_rate_for_claim_item(source, target)

	return {
		"doctype": "Sales Invoice Item",
		"field_map": {
			"name": "sales_order_item",
			"parent": "sales_order",
			"quotation": "quotation",
			"quotation_item": "quotation_item",
		},
		"postprocess": update_item,
		"condition": item_condition,
	}


def get_unbilled_dn_qty_map(sales_order):
	unbilled_dn_qty_map = {}

	item_data = frappe.db.sql("""
		select sales_order_item, qty - billed_qty
		from `tabDelivery Note Item`
		where sales_order=%s and docstatus=1
	""", sales_order)

	for sales_order_item, qty in item_data:
		if not unbilled_dn_qty_map.get(sales_order_item):
			unbilled_dn_qty_map[sales_order_item] = 0
		unbilled_dn_qty_map[sales_order_item] += qty

	return unbilled_dn_qty_map


@frappe.whitelist()
def get_events(start, end, filters=None):
	"""Returns events for Gantt / Calendar view rendering.

	:param start: Start date-time.
	:param end: End date-time.
	:param filters: Filters (JSON).
	"""
	from frappe.desk.calendar import get_event_conditions
	conditions = get_event_conditions("Sales Order", filters)

	data = frappe.db.sql("""
		select
			distinct `tabSales Order`.name, `tabSales Order`.customer_name, `tabSales Order`.status,
			`tabSales Order`.delivery_status, `tabSales Order`.billing_status, `tabSales Order`.delivery_status,
			`tabSales Order Item`.delivery_date
		from
			`tabSales Order`, `tabSales Order Item`
		where `tabSales Order`.name = `tabSales Order Item`.parent
			and `tabSales Order`.skip_delivery_note = 0
			and `tabSales Order Item`.skip_delivery_note = 0
			and (ifnull(`tabSales Order Item`.delivery_date, '0000-00-00')!= '0000-00-00') \
			and (`tabSales Order Item`.delivery_date between %(start)s and %(end)s)
			and `tabSales Order`.docstatus < 2
			{conditions}
		""".format(conditions=conditions), {
			"start": start,
			"end": end
		}, as_dict=True, update={"allDay": 0})
	return data


@frappe.whitelist()
def make_purchase_order(source_name, for_supplier=None, selected_items=[], target_doc=None):
	if isinstance(selected_items, str):
		selected_items = json.loads(selected_items)

	def item_condition(source, source_parent, target_parent):
		return source.ordered_qty < source.qty and source.supplier == supplier and source.item_code in selected_items

	def set_missing_values(source, target):
		target.supplier = supplier
		target.apply_discount_on = ""
		target.additional_discount_percentage = 0.0
		target.discount_amount = 0.0
		target.inter_company_reference = ""

		default_price_list = frappe.get_value("Supplier", supplier, "default_price_list")
		if default_price_list:
			target.buying_price_list = default_price_list

		if any(d.delivered_by_supplier for d in source.items):
			if source.shipping_address_name:
				target.shipping_address = source.shipping_address_name
				target.shipping_address_display = source.shipping_address
			else:
				target.shipping_address = source.customer_address
				target.shipping_address_display = source.address_display

			target.customer_contact_person = source.contact_person
			target.customer_contact_display = source.contact_display
			target.customer_contact_mobile = source.contact_mobile
			target.customer_contact_email = source.contact_email

		else:
			target.customer = ""
			target.customer_name = ""

		target.run_method("postprocess_after_mapping")

	def update_item(source, target, source_parent, target_parent):
		target.schedule_date = source.delivery_date
		target.qty = flt(source.qty) - flt(source.ordered_qty)
		target.project = source_parent.project

	suppliers = []
	if for_supplier:
		suppliers.append(for_supplier)
	else:
		sales_order = frappe.get_doc("Sales Order", source_name)
		for item in sales_order.items:
			if item.supplier and item.supplier not in suppliers:
				suppliers.append(item.supplier)

	if not suppliers:
		frappe.throw(_("Please set a Supplier against the Items to be considered in the Purchase Order."))

	for supplier in suppliers:
		po = frappe.get_all("Purchase Order", filters={"sales_order": source_name, "supplier": supplier, "docstatus": ("<", 2)})
		if len(po) == 0:
			doc = get_mapped_doc("Sales Order", source_name, {
				"Sales Order": {
					"doctype": "Purchase Order",
					"field_no_map": [
						"address_display",
						"contact_display",
						"contact_mobile",
						"contact_email",
						"contact_person",
						"taxes_and_charges",
						"currency",
						"transaction_type"
					],
					"field_map": [
						['remarks', 'remarks']
					],
					"validation": {
						"docstatus": ["=", 1]
					}
				},
				"Sales Order Item": {
					"doctype": "Purchase Order Item",
					"field_map":  [
						["name", "sales_order_item"],
						["parent", "sales_order"],
						["stock_uom", "stock_uom"],
						["uom", "uom"],
						["conversion_factor", "conversion_factor"],
						["delivery_date", "schedule_date"]
					],
					"field_no_map": [
						"rate",
						"price_list_rate",
						"item_tax_template"
					],
					"postprocess": update_item,
					"condition": item_condition,
				}
			}, target_doc, set_missing_values)

			if not for_supplier:
				doc.insert()
		else:
			suppliers = []

	if suppliers:
		return doc
	else:
		frappe.msgprint(_("Purchase Order already created for all Sales Order Items"))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_supplier(doctype, txt, searchfield, start, page_len, filters):
	supp_master_name = frappe.defaults.get_user_default("supp_master_name")
	if supp_master_name == "Supplier Name":
		fields = ["name", "supplier_group"]
	else:
		fields = ["name", "supplier_name", "supplier_group"]
	fields = ", ".join(fields)

	return frappe.db.sql("""
		select {field} from `tabSupplier`
		where docstatus < 2
			and ({key} like %(txt)s
				or supplier_name like %(txt)s)
			and name in (select supplier from `tabSales Order Item` where parent = %(parent)s)
			and name not in (select supplier from `tabPurchase Order` po inner join `tabPurchase Order Item` poi
				on po.name=poi.parent where po.docstatus<2 and poi.sales_order=%(parent)s)
		order by
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
			if(locate(%(_txt)s, supplier_name), locate(%(_txt)s, supplier_name), 99999),
			name, supplier_name
		limit %(start)s, %(page_len)s
	""".format(**{
			'field': fields,
			'key': frappe.db.escape(searchfield)
		}), {
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace("%", ""),
			'start': start,
			'page_len': page_len,
			'parent': filters.get('parent')
		})


@frappe.whitelist()
def make_raw_material_request(items, company, sales_order, project=None):
	if not frappe.has_permission("Sales Order", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if isinstance(items, str):
		items = frappe._dict(json.loads(items))

	for item in items.get('items'):
		item["include_exploded_items"] = items.get("include_exploded_items")
		item["ignore_existing_ordered_qty"] = items.get("ignore_existing_ordered_qty")
		item["include_raw_materials_from_sales_order"] = items.get("include_raw_materials_from_sales_order")

	items.update({
		"company": company,
		"sales_order": sales_order
	})

	raw_materials = get_items_for_material_requests(items)
	if not raw_materials:
		frappe.msgprint(_("Material Request not created, as quantity for Raw Materials already available."))
		return

	material_request = frappe.new_doc('Material Request')
	material_request.update(dict(
		doctype = 'Material Request',
		transaction_date = nowdate(),
		company = company,
	))
	for item in raw_materials:
		item_doc = frappe.get_cached_doc('Item', item.get('item_code'))

		schedule_date = add_days(nowdate(), cint(item_doc.lead_time_days))
		row = material_request.append('items', {
			'item_code': item.get('item_code'),
			'qty': item.get('quantity'),
			'required_qty': flt(item.get('required_qty')),
			'schedule_date': schedule_date,
			'warehouse': item.get('warehouse'),
			'sales_order': sales_order,
			'project': project
		})

	material_request.flags.ignore_permissions = 1
	material_request.run_method("postprocess_after_mapping")
	return material_request


@frappe.whitelist()
def make_inter_company_purchase_order(source_name, target_doc=None):
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_inter_company_transaction
	return make_inter_company_transaction("Sales Order", source_name, target_doc)


@frappe.whitelist()
def create_pick_list(source_name, target_doc=None):
	def update_item_quantity(source, target, source_parent, target_parent):
		target.qty = flt(source.qty) - flt(source.delivered_qty)
		target.stock_qty = (flt(source.qty) - flt(source.delivered_qty)) * flt(source.conversion_factor)

	doc = get_mapped_doc('Sales Order', source_name, {
		'Sales Order': {
			'doctype': 'Pick List',
			'validation': {
				'docstatus': ['=', 1]
			}
		},
		'Sales Order Item': {
			'doctype': 'Pick List Item',
			'field_map': {
				'parent': 'sales_order',
				'name': 'sales_order_item'
			},
			'postprocess': update_item_quantity,
			'condition': lambda doc, source, target: abs(doc.delivered_qty) < abs(doc.qty) and not doc.skip_delivery_note
		},
	}, target_doc)

	doc.purpose = 'Delivery'

	doc.set_item_locations()

	return doc
