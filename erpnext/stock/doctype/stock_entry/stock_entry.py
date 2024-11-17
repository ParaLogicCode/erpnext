# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe, erpnext
import frappe.defaults
from frappe import _
from frappe.utils import cstr, cint, flt, comma_or, getdate, nowdate
from erpnext.stock.utils import get_incoming_rate, get_latest_stock_qty
from erpnext.stock.stock_ledger import get_previous_sle, get_valuation_rate
from erpnext.stock.get_item_details import get_bin_details, get_default_cost_center, get_conversion_factor,\
	get_reserved_qty_for_so, get_hide_item_code, get_default_warehouse
from erpnext.stock.doctype.batch.batch import get_batch_qty, auto_select_and_split_batches, validate_batch_no
from erpnext.stock.doctype.item_alternative.item_alternative import has_alternative_item, get_available_alternative_items
from erpnext.manufacturing.doctype.bom.bom import validate_bom_no, add_additional_cost
from erpnext.manufacturing.doctype.work_order.work_order import get_qty_with_allowance
from frappe.model.mapper import get_mapped_doc
from erpnext.stock.doctype.serial_no.serial_no import update_serial_nos_after_submit, get_serial_nos
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import OpeningEntryAccountError
from erpnext.controllers.transaction_controller import TransactionController
import json


class IncorrectValuationRateError(frappe.ValidationError): pass
class DuplicateEntryForWorkOrderError(frappe.ValidationError): pass
class OperationsNotCompleteError(frappe.ValidationError): pass
class MaxSampleAlreadyRetainedError(frappe.ValidationError): pass


form_grid_templates = {
	"items": "templates/form_grid/stock_entry_grid.html"
}


class StockEntry(TransactionController):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.force_item_fields = [
			"stock_uom", "has_batch_no", "has_serial_no", "is_vehicle", "alt_uom", "alt_uom_size"
		]

	def get_feed(self):
		return self.stock_entry_type

	def onload(self):
		for item in self.get("items"):
			item.update(get_bin_details(item.item_code, item.s_warehouse))

	def before_print(self, print_settings=None):
		super().before_print(print_settings)

		if self.get("purchase_order"):
			self.po_no = self.purchase_order
			self.po_date = frappe.db.get_value("Purchase Order", self.purchase_order, "transaction_date")

		self.s_warehouses = list(set([frappe.get_cached_value("Warehouse", item.s_warehouse, 'warehouse_name')
			for item in self.items if item.get('s_warehouse')]))
		self.t_warehouses = list(set([frappe.get_cached_value("Warehouse", item.t_warehouse, 'warehouse_name')
			for item in self.items if item.get('t_warehouse')]))

	def validate(self):
		self.get_work_order()
		self.validate_posting_time()
		self.validate_stock_entry_type()
		self.validate_purpose()
		self.validate_vehicle_item()
		self.validate_item()
		self.validate_customer_provided_item()
		self.validate_customer_provided_entry()
		self.validate_qty()
		self.set_stock_qty()
		self.validate_uom_is_integer("uom", "qty")
		self.validate_uom_is_integer("stock_uom", "stock_qty")
		self.validate_warehouse()
		self.validate_work_order()
		self.validate_bom()
		self.validate_finished_goods()
		self.validate_with_material_request()
		self.validate_packing_slips()
		self.validate_batch()
		self.validate_inspection()
		self.validate_fg_completed_qty()
		self.validate_difference_account()
		self.set_job_card_data()
		self.set_purpose_for_stock_entry()

		if not self.from_bom:
			self.fg_completed_qty = 0.0

		self.set_incoming_rate()
		self.validate_serialized_batch()
		self.set_actual_qty()
		self.calculate_rate_and_amount()
		self.set_transferred_status()

	def before_submit(self):
		self.validate_purchase_order_raw_material_qty()

	def on_submit(self):
		self.auto_create_batches("t_warehouse",
			item_condition=lambda d: not d.s_warehouse,
			set_manufacturing_date=self.purpose in ("Manufacture", "Repack"))
		self.update_stock_ledger()
		self.update_packing_slips()
		update_serial_nos_after_submit(self, "items")
		self.update_work_order()
		self.update_purchase_order_supplied_items()
		self.make_gl_entries()
		self.update_cost_in_project()
		self.validate_reserved_serial_no_consumption()
		self.update_previous_doc_status()
		self.update_quality_inspection()
		self.update_sales_order_in_serial_nos()

	def on_cancel(self):
		self.update_purchase_order_supplied_items()
		self.update_stock_ledger()
		self.update_packing_slips()
		self.make_gl_entries_on_cancel()
		self.update_work_order()
		self.update_cost_in_project()
		self.update_previous_doc_status()
		self.update_quality_inspection()
		self.unlink_auto_created_batches()

	def get_work_order(self):
		if self.get("pro_doc"):
			return self.pro_doc

		self.pro_doc = frappe._dict()
		if self.work_order:
			self.pro_doc = frappe.get_doc("Work Order", self.work_order)

		return self.pro_doc

	def update_previous_doc_status(self):
		material_requests = set()
		material_request_row_names = set()
		stock_entries = set()
		stock_entry_row_names = set()

		for d in self.get("items"):
			if d.material_request:
				material_requests.add(d.material_request)
			if d.material_request_item:
				material_request_row_names.add(d.material_request_item)
			if d.against_stock_entry:
				stock_entries.add(d.against_stock_entry)
			if d.ste_detail:
				stock_entry_row_names.add(d.ste_detail)

		# Update Material Requests
		for name in material_requests:
			doc = frappe.get_doc("Material Request", name)

			if doc.docstatus != 1:
				frappe.throw(_("{0} is not submitted").format(frappe.get_desk_link("Material Request", name)),
					frappe.InvalidStatusError)

			if doc.status == "Stopped":
				frappe.throw(_("{0} is cancelled or stopped").format(frappe.get_desk_link("Material Request", name)),
					frappe.InvalidStatusError)

			doc.set_completion_status(update=True)
			doc.validate_ordered_qty(from_doctype=self.doctype, row_names=material_request_row_names)
			doc.update_requested_qty(material_request_row_names)
			doc.set_status(update=True)
			doc.notify_update()

		# Update Send to Warehouse Stock Entries
		if self.purpose == "Receive at Warehouse":
			for name in stock_entries:
				doc = frappe.get_doc("Stock Entry", name)
				doc.set_transferred_status(update=True)
				doc.validate_transferred_qty(from_doctype=self.doctype, row_names=stock_entry_row_names)
				doc.set_status(update=True)
				doc.notify_update()

	def set_transferred_status(self, update=False, update_modified=True):
		transferred_qty_map = self.get_transferred_qty_map()

		# update values in rows
		for d in self.items:
			d.transferred_qty = flt(transferred_qty_map.get(d.name))

			if update:
				d.db_set({
					'transferred_qty': d.transferred_qty,
				}, update_modified=update_modified)

		# update percentage in parent
		self.per_transferred = self.calculate_status_percentage('transferred_qty', 'qty', self.items)

		if update:
			self.db_set({
				'per_transferred': self.per_transferred,
			}, update_modified=update_modified)

	def get_transferred_qty_map(self):
		transferred_qty_map = {}

		if self.docstatus == 1:
			row_names = [d.name for d in self.items]
			if row_names:
				transferred_qty_map = dict(frappe.db.sql("""
					select i.ste_detail, sum(i.qty)
					from `tabStock Entry Detail` i
					inner join `tabStock Entry` p on p.name = i.parent
					where p.docstatus = 1 and i.against_stock_entry = %s and i.ste_detail in %s
					group by i.ste_detail
				""", [self.name, row_names]))

		return transferred_qty_map

	def validate_transferred_qty(self, from_doctype=None, row_names=None):
		self.validate_completed_qty('transferred_qty', 'qty', self.items,
			allowance_type=None, from_doctype=from_doctype, row_names=row_names)

	@frappe.whitelist()
	def auto_select_batches(self, postprocess=True, exclude_item_codes=None):
		auto_select_and_split_batches(
			self,
			warehouse_field='s_warehouse',
			additional_group_fields=[
				"material_request", "material_request_item",
				"subcontracted_item", "purchase_order_item",
				"against_stock_entry", "ste_detail"
			],
			exclude_item_codes=exclude_item_codes,
		)

		if cint(postprocess):
			self.set_stock_qty()
			self.calculate_rate_and_amount(raise_error_if_no_rate=False)

	def set_job_card_data(self):
		if self.job_card and not self.work_order:
			data = frappe.db.get_value('Job Card',
				self.job_card, ['for_quantity', 'work_order', 'bom_no'], as_dict=1)
			self.fg_completed_qty = data.for_quantity
			self.work_order = data.work_order
			self.from_bom = 1
			self.bom_no = data.bom_no

	def validate_stock_entry_type(self):
		ste_type_doc = frappe.get_cached_doc("Stock Entry Type", self.stock_entry_type)
		self.purpose = ste_type_doc.purpose

		if ste_type_doc.posting_date:
			self.posting_date = ste_type_doc.posting_date

		if ste_type_doc.is_opening:
			self.is_opening = ste_type_doc.is_opening

		if ste_type_doc.customer_provided:
			self.customer_provided = cint(ste_type_doc.customer_provided == "Yes")

		self.source_warehouse_type = ste_type_doc.source_warehouse_type
		self.target_warehouse_type = ste_type_doc.target_warehouse_type
		for d in self.items:
			if self.source_warehouse_type and d.s_warehouse:
				if frappe.get_cached_value("Warehouse", d.s_warehouse, "warehouse_type") != self.source_warehouse_type:
					frappe.throw(_("Row #{0}: Source Warehouse must be of type {1} for Stock Entry Type {2}")
						.format(d.idx, frappe.bold(self.source_warehouse_type), frappe.bold(self.stock_entry_type)))
			if self.target_warehouse_type and d.t_warehouse:
				if frappe.get_cached_value("Warehouse", d.t_warehouse, "warehouse_type") != self.target_warehouse_type:
					frappe.throw(_("Row #{0}: Target Warehouse must be of type {1} for Stock Entry Type {2}")
						.format(d.idx, frappe.bold(self.target_warehouse_type), frappe.bold(self.stock_entry_type)))

	def validate_purpose(self):
		valid_purposes = ["Material Issue", "Material Receipt", "Material Transfer",
			"Material Transfer for Manufacture", "Manufacture", "Repack", "Send to Subcontractor",
			"Material Consumption for Manufacture", "Send to Warehouse", "Receive at Warehouse"]

		if self.purpose not in valid_purposes:
			frappe.throw(_("Purpose must be one of {0}").format(comma_or(valid_purposes)))

		if self.job_card and self.purpose not in ("Material Transfer for Manufacture", "Material Consumption for Manufacture"):
			frappe.throw(_("For Job Card {0}, {1} type Stock Entry is not allowed").format(self.job_card, self.purpose))

		if self.purpose != "Manufacture":
			self.consumed_materials = []

	def set_stock_qty(self):
		self.total_qty = 0
		self.total_stock_qty = 0
		self.total_alt_uom_qty = 0

		has_target_warehouse = any(d.t_warehouse for d in self.get("items") if d.t_warehouse)

		for item in self.get("items"):
			if not flt(item.conversion_factor):
				frappe.throw(_("Row {0}: UOM Conversion Factor is mandatory").format(item.idx))

			item.stock_qty = flt(flt(item.qty) * flt(item.conversion_factor), 6)

			if not item.alt_uom:
				item.alt_uom_size = 1.0
			item.alt_uom_qty = flt(flt(item.qty) * flt(item.conversion_factor) * flt(item.alt_uom_size),
				item.precision("alt_uom_qty"))

			if not has_target_warehouse or item.t_warehouse:
				self.total_qty += flt(item.qty)
				self.total_stock_qty += flt(item.stock_qty)
				self.total_alt_uom_qty += flt(item.alt_uom_qty)

		self.round_floats_in(self, ['total_qty', 'total_alt_uom_qty', 'total_stock_qty'])

	def update_cost_in_project(self):
		if self.work_order:
			if not frappe.db.get_value("Work Order", self.work_order, "update_consumed_material_cost_in_project"):
				return

		if self.project:
			project = frappe.get_doc("Project", self.project)
			project.set_material_consumed_cost(update=True)
			project.set_gross_margin(update=True)
			project.set_status(update=True)
			project.notify_update()

	def set_missing_values(self, for_validate=False):
		for d in self.get("items"):
			self.set_missing_item_values(d)

		self.set_stock_qty()

	def validate_item(self):
		from erpnext.stock.doctype.item.item import validate_end_of_life

		stock_items = self.get_stock_items()
		serialized_items = self.get_serialized_items()

		for d in self.get("items"):
			item = frappe.get_cached_value("Item", d.item_code, ['has_variants', 'end_of_life', 'disabled'], as_dict=1)
			validate_end_of_life(d.item_code, end_of_life=item.end_of_life, disabled=item.disabled)

			if cint(item.has_variants):
				frappe.throw(_("Row #{0}: {1} is a template Item, please select one of its variants")
					.format(d.idx, frappe.bold(d.item_code)))

			if d.item_code not in stock_items:
				frappe.throw(_("Row #{0}: {1} is not a stock Item")
					.format(d.idx, frappe.bold(d.item_code)))

			if flt(d.qty) and flt(d.qty) < 0:
				frappe.throw(_("Row #{0}: Item {1}, quantity must be positive number")
					.format(d.idx, frappe.bold(d.item_code)))

			self.set_missing_item_values(d)

			if not d.stock_qty and d.qty:
				d.stock_qty = flt(flt(d.qty) * flt(d.conversion_factor), 6)

			if (self.purpose in ("Material Transfer", "Material Transfer for Manufacture")
					and not d.serial_no
					and d.item_code in serialized_items):
				frappe.throw(_("Row #{0}: Please specify Serial No for Item {1}").format(d.idx, d.item_code),
					frappe.MandatoryError)

	def set_missing_item_values(self, item):
		item_details = self.get_item_details(frappe._dict({
			"item_code": item.item_code,
			"company": self.company,
			"batch_no": item.batch_no,
			"project": item.project or self.project,
			"uom": item.uom,
			"s_warehouse": item.s_warehouse,
			"subcontracted_item": item.subcontracted_item,
		}), for_update=True)

		for f in item_details:
			if f in self.force_item_fields or not item.get(f):
				item.set(f, item_details.get(f))

	def validate_qty(self):
		for d in self.get("items"):
			if not flt(d.qty):
				frappe.throw(_("Row {0}: Qty is mandatory").format(d.idx))

	def validate_fg_completed_qty(self):
		if self.purpose != "Manufacture" or not self.work_order:
			return

		self.get_work_order()
		for item in self.items:
			if item.item_code == self.pro_doc.production_item and item.qty != self.fg_completed_qty:
				frappe.throw(_("Finished product quantity {0} and For Quantity {1} cannot be different").format(
					frappe.bold(item.get_formatted("qty")), frappe.bold(self.get_formatted("fg_completed_qty"))
				))

	def validate_difference_account(self):
		if not cint(erpnext.is_perpetual_inventory_enabled(self.company)):
			return

		for d in self.get("items"):
			if not d.expense_account:
				frappe.throw(_("Please enter <b>Difference Account</b> or set default <b>Stock Adjustment Account</b> for company {0}")
					.format(frappe.bold(self.company)))

			elif self.is_opening == "Yes" and frappe.db.get_value("Account", d.expense_account, "report_type") == "Profit and Loss":
				frappe.throw(_("Difference Account must be a Asset/Liability type account, since this Stock Entry is an Opening Entry"), OpeningEntryAccountError)

	def validate_warehouse(self):
		"""perform various (sometimes conditional) validations on warehouse"""

		source_mandatory = ["Material Issue", "Material Transfer", "Send to Subcontractor", "Material Transfer for Manufacture",
			"Material Consumption for Manufacture", "Send to Warehouse", "Receive at Warehouse"]

		target_mandatory = ["Material Receipt", "Material Transfer", "Send to Subcontractor",
			"Material Transfer for Manufacture", "Send to Warehouse", "Receive at Warehouse"]

		validate_for_manufacture = any([d.bom_no for d in self.get("items")])

		if self.purpose in source_mandatory and self.purpose not in target_mandatory:
			self.to_warehouse = None
			for d in self.get('items'):
				d.t_warehouse = None
		elif self.purpose in target_mandatory and self.purpose not in source_mandatory:
			self.from_warehouse = None
			for d in self.get('items'):
				d.s_warehouse = None

		for d in self.get('items'):
			if not d.s_warehouse and not d.t_warehouse:
				d.s_warehouse = self.from_warehouse
				d.t_warehouse = self.to_warehouse

			if not (d.s_warehouse or d.t_warehouse):
				frappe.throw(_("Atleast one warehouse is mandatory"))

			if self.purpose in source_mandatory and not d.s_warehouse:
				if self.from_warehouse:
					d.s_warehouse = self.from_warehouse
				else:
					frappe.throw(_("Source Warehouse is mandatory for row {0}").format(d.idx))

			if self.purpose in target_mandatory and not d.t_warehouse:
				if self.to_warehouse:
					d.t_warehouse = self.to_warehouse
				else:
					frappe.throw(_("Target Warehouse is mandatory for row {0}").format(d.idx))

			if self.purpose == "Manufacture":
				if validate_for_manufacture:
					if d.bom_no:
						d.s_warehouse = None
						if not d.t_warehouse:
							frappe.throw(_("Target Warehouse is mandatory for row {0}").format(d.idx))
					else:
						d.t_warehouse = None
						if not d.s_warehouse:
							frappe.throw(_("Source Warehouse is mandatory for row {0}").format(d.idx))

			if cstr(d.s_warehouse) == cstr(d.t_warehouse) and not self.purpose == "Material Transfer for Manufacture":
				frappe.throw(_("Source and Target Warehouse cannot be same for row {0}").format(d.idx))

	def validate_customer_provided_entry(self):
		if self.purpose not in ('Material Receipt', 'Material Issue'):
			self.customer_provided = 0

		if not self.customer_provided:
			self.customer = self.customer_name = self.customer_address = None
		if not self.customer_address and not self.supplier_address:
			self.address_display = None

		if self.customer_provided:
			for d in self.items:
				d.allow_zero_valuation_rate = 1

	def validate_work_order(self):
		if self.purpose not in ("Material Transfer", "Material Transfer for Manufacture", "Material Consumption for Manufacture", "Manufacture"):
			self.work_order = None

		if self.purpose in ("Manufacture", "Material Consumption for Manufacture") or (self.purpose == "Material Transfer for Manufacture" and self.from_bom):
			if not self.fg_completed_qty:
				frappe.throw(_("Production Qty is mandatory"))

		for d in self.items:
			if self.work_order:
				d.work_order = None

			if d.get("work_order"):
				wo_details = frappe.db.get_value("Work Order", d.work_order, [
					"name", "production_item",
				], as_dict=1)

				if not wo_details:
					frappe.throw(_("Row #{0}: Work Order {1} does not exist").format(d.idx, d.work_order))
				if d.item_code != wo_details.production_item:
					frappe.throw(_("Row #{0}: Item {1} does not match with {2}. Item Code must be {3}").format(
						d.idx,
						frappe.bold(d.item_code),
						frappe.get_desk_link("Work Order", d.work_order),
						frappe.bold(wo_details.production_item),
					))

	def set_incoming_rate(self):
		for d in self.items:
			if d.s_warehouse:
				args = self.get_args_for_incoming_rate(d)
				d.basic_rate = get_incoming_rate(args)
			elif d.allow_zero_valuation_rate and not d.s_warehouse:
				d.basic_rate = 0.0
			elif d.t_warehouse and not d.basic_rate and not self.is_finished_good_item(d):
				d.basic_rate = get_valuation_rate(d.item_code, d.t_warehouse,
					self.doctype, d.name, d.batch_no, d.allow_zero_valuation_rate,
					currency=erpnext.get_company_currency(self.company), company=self.company,
					raise_error_if_no_rate=False)

	def set_actual_qty(self):
		for d in self.get('items'):
			previous_sle = get_previous_sle({
				"item_code": d.item_code,
				"warehouse": d.s_warehouse or d.t_warehouse,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time
			})

			# get actual stock at source warehouse
			d.actual_qty = previous_sle.get("qty_after_transaction") or 0

	@frappe.whitelist()
	def get_stock_and_rate(self):
		self.set_work_order_details()
		self.set_stock_qty()
		self.set_actual_qty()
		self.calculate_rate_and_amount()

	def calculate_rate_and_amount(self, force=False,
			update_finished_item_rate=True, raise_error_if_no_rate=True):
		self.set_basic_rate(force, update_finished_item_rate, raise_error_if_no_rate)
		self.distribute_additional_costs()
		self.update_valuation_rate()
		self.set_total_incoming_outgoing_value()
		self.set_total_amount()

	def set_basic_rate(self, force=False, update_finished_item_rate=True, raise_error_if_no_rate=True):
		"""get stock and incoming rate on posting date"""
		for d in self.get('items'):
			if self.customer_provided and not d.s_warehouse:
				d.basic_rate = 0
				d.basic_amount = 0

			args = self.get_args_for_incoming_rate(d)

			# get basic rate
			if not d.bom_no:
				if d.s_warehouse or force:
					basic_rate = flt(get_incoming_rate(args, raise_error_if_no_rate))
					if basic_rate > 0:
						d.basic_rate = basic_rate

				d.basic_amount = flt(flt(d.stock_qty) * flt(d.basic_rate), d.precision("basic_amount"))

			# get scrap items basic rate
			if self.is_scrap_item(d):
				if not flt(d.basic_rate) and not d.allow_zero_valuation_rate:
					basic_rate = flt(get_incoming_rate(args, raise_error_if_no_rate))
					if basic_rate > 0:
						d.basic_rate = basic_rate
					d.basic_amount = flt(flt(d.stock_qty) * flt(d.basic_rate), d.precision("basic_amount"))

		if self.purpose in ("Manufacture", "Repack") and update_finished_item_rate:
			self.set_basic_rate_for_finished_goods()

	def get_args_for_incoming_rate(self, item):
		return frappe._dict({
			"item_code": item.item_code,
			"warehouse": item.s_warehouse or item.t_warehouse,
			"batch_no": item.batch_no,
			"posting_date": self.posting_date,
			"posting_time": self.posting_time,
			"qty": item.s_warehouse and -1*flt(item.stock_qty) or flt(item.stock_qty),
			"serial_no": item.serial_no,
			"voucher_type": self.doctype,
			"voucher_no": self.name,
			"company": self.company,
			"allow_zero_valuation": item.allow_zero_valuation_rate,
		})

	def set_basic_rate_for_finished_goods(self):
		for d in self.get("items"):
			d.cost_percentage = 0

		if self.purpose not in ["Manufacture", "Repack"]:
			return

		# Calculate costs and qtys
		raw_material_cost = 0
		scrap_material_cost = 0
		total_fg_qty = 0
		total_fg_stock_qty = 0
		total_fg_items = 0
		for d in self.get("items"):
			if self.is_scrap_item(d):
				scrap_material_cost += flt(d.basic_amount)
			elif d.s_warehouse and not d.t_warehouse:
				raw_material_cost += flt(d.basic_amount)
			elif self.is_finished_good_item(d):
				total_fg_qty += flt(d.qty)
				total_fg_stock_qty += flt(d.stock_qty)
				total_fg_items += 1

		if not total_fg_stock_qty or not total_fg_items:
			return

		# set fg rate
		consumed_material_cost = sum(flt(d.amount) for d in self.get("consumed_materials"))
		total_cost = raw_material_cost + consumed_material_cost - scrap_material_cost
		if self.purpose == "Manufacture":
			basic_rate = total_cost / total_fg_stock_qty if total_fg_stock_qty else 0
		else:
			basic_rate = total_cost / total_fg_qty if total_fg_qty else 0

		for d in self.get("items"):
			if self.is_finished_good_item(d) and not d.set_basic_rate_manually:
				if self.purpose == "Manufacture":
					d.basic_rate = basic_rate
					d.basic_amount = flt(d.basic_rate * flt(d.stock_qty), d.precision("basic_amount"))
					d.cost_percentage = flt(d.stock_qty) / total_fg_stock_qty * 100 if total_fg_stock_qty else 0
				else:
					d.basic_rate = basic_rate
					d.basic_amount = flt(d.basic_rate * flt(d.qty), d.precision("basic_amount"))
					d.cost_percentage = flt(d.qty) / total_fg_qty * 100 if total_fg_qty else 0

	def is_scrap_item(self, d):
		pro_doc = getattr(self, "pro_doc", frappe._dict())
		return (self.purpose in ('Manufacture', 'Repack')
			and d.bom_no
			and d.t_warehouse
			and pro_doc.scrap_warehouse == d.t_warehouse)

	def is_finished_good_item(self, d):
		return (self.purpose in ('Manufacture', 'Repack')
			and (d.bom_no or d.t_warehouse)
			and not d.s_warehouse
			and not self.is_scrap_item(d))

	def distribute_additional_costs(self):
		if self.purpose == "Material Issue":
			self.additional_costs = []

		self.total_additional_costs = sum([flt(t.amount) for t in self.get("additional_costs")])
		total_basic_amount = sum([flt(t.basic_amount) for t in self.get("items") if t.t_warehouse and not self.is_scrap_item(t)])
		total_qty = sum([flt(t.qty) for t in self.get("items") if t.t_warehouse and not self.is_scrap_item(t)])

		for d in self.get("items"):
			if d.t_warehouse and not self.is_scrap_item(d) and (total_basic_amount or total_qty):
				if total_basic_amount:
					d.additional_cost = (flt(d.basic_amount) / total_basic_amount) * self.total_additional_costs
				else:
					d.additional_cost = (flt(d.qty) / total_qty) * self.total_additional_costs
			else:
				d.additional_cost = 0

	def update_valuation_rate(self):
		for d in self.get("items"):
			if d.stock_qty:
				d.amount = flt(flt(d.basic_amount) + flt(d.additional_cost), d.precision("amount"))
				d.valuation_rate = d.amount / flt(d.stock_qty)

	def set_total_incoming_outgoing_value(self):
		self.total_incoming_value = self.total_outgoing_value = 0.0
		for d in self.get("items"):
			if d.t_warehouse:
				self.total_incoming_value += flt(d.amount)
			if d.s_warehouse:
				self.total_outgoing_value += flt(d.amount)

		self.value_difference = self.total_incoming_value - self.total_outgoing_value

	def set_total_amount(self):
		self.total_amount = None
		if self.purpose not in ['Manufacture', 'Repack']:
			self.total_amount = sum([flt(item.amount) for item in self.get("items")])

	def set_stock_entry_type(self):
		if self.purpose:
			types = frappe.db.get_all("Stock Entry Type", filters={'purpose': self.purpose}, order_by='creation asc')
			types = [d.name for d in types]

			if self.purpose in types:
				self.stock_entry_type = self.purpose
			else:
				self.stock_entry_type = types[0]

	def set_purpose_for_stock_entry(self):
		if self.stock_entry_type and not self.purpose:
			self.purpose = frappe.get_cached_value('Stock Entry Type',
				self.stock_entry_type, 'purpose')

	def validate_purchase_order_raw_material_qty(self):
		if self.purpose == "Send to Subcontractor":
			super().validate_purchase_order_raw_material_qty()

	def validate_bom(self):
		for d in self.get('items'):
			if d.bom_no and (d.t_warehouse != getattr(self, "pro_doc", frappe._dict()).scrap_warehouse):
				item_code = d.original_item or d.item_code
				validate_bom_no(item_code, d.bom_no)

	def validate_finished_goods(self):
		"""validation: finished good quantity should be same as manufacturing quantity"""
		if not self.work_order:
			return

		self.get_work_order()

		items_with_target_warehouse = []

		for d in self.get('items'):
			if (self.purpose != "Send to Subcontractor" and d.bom_no
				and flt(d.stock_qty) > flt(self.fg_completed_qty) and d.item_code == self.pro_doc.production_item):
				frappe.throw(_("Quantity in row {0} ({1}) must be same as manufactured quantity {2}"). \
					format(d.idx, d.stock_qty, self.fg_completed_qty))

			if self.work_order and self.purpose == "Manufacture" and d.t_warehouse:
				items_with_target_warehouse.append(d.item_code)

		if self.work_order and self.purpose == "Manufacture":
			allowed_qty = get_qty_with_allowance(self.pro_doc.qty, self.pro_doc.qty, self.pro_doc.max_qty)

			if flt(self.fg_completed_qty) > flt(allowed_qty, self.precision("fg_completed_qty")):
				frappe.throw(_("For quantity {0} should not be greater than work order quantity {1}")
					.format(flt(self.fg_completed_qty), self.pro_doc.qty))

			if self.pro_doc.production_item not in items_with_target_warehouse:
				frappe.throw(_("Finished Item {0} must be entered for Manufacture type entry")
					.format(self.pro_doc.production_item))

	def update_stock_ledger(self, allow_negative_stock=False):
		sl_entries = []

		# make sl entries for source warehouse first, then do for target warehouse
		for d in self.get('items'):
			if cstr(d.s_warehouse):
				sl_entries.append(self.get_sl_entries(d, {
					"warehouse": cstr(d.s_warehouse),
					"actual_qty": -flt(d.stock_qty),
					"incoming_rate": 0,
					"is_transfer": cint(bool(d.s_warehouse and d.t_warehouse)),
				}))

		for d in self.get('items'):
			if cstr(d.t_warehouse):
				sle = self.get_sl_entries(d, {
					"warehouse": cstr(d.t_warehouse),
					"actual_qty": flt(d.stock_qty),
					"incoming_rate": flt(d.valuation_rate),
					"packing_slip": d.packing_slip if self.purpose == "Material Transfer" else None,
					"is_transfer": cint(bool(d.s_warehouse and d.t_warehouse)),
				})

				# SLE Dependency
				if self.docstatus == 1:
					# Material Transfer dependency
					if cstr(d.s_warehouse):
						sle.additional_cost = flt(d.additional_cost)
						sle.dependencies = [{
							"dependent_voucher_type": self.doctype,
							"dependent_voucher_no": self.name,
							"dependent_voucher_detail_no": d.name,
							"dependency_type": "Amount",
						}]

					# Receive at Warehouse dependency
					elif d.against_stock_entry and d.ste_detail:
						sle.dependencies = [{
							"dependent_voucher_type": d.against_stock_entry,
							"dependent_voucher_no": d.ste_detail,
							"dependent_voucher_detail_no": d.name,
							"dependency_type": "Rate",
							"dependency_qty_filter": "Positive"
						}]

					# Manufacture / Repack dependency
					elif self.is_finished_good_item(d) and not d.set_basic_rate_manually and d.cost_percentage:
						sle.additional_cost = flt(d.additional_cost)
						sle.dependencies = []
						for dep_row in self.get("items"):
							if flt(dep_row.stock_qty) and (self.is_scrap_item(dep_row) or (dep_row.s_warehouse and not dep_row.t_warehouse)):
								sle.dependencies.append({
									"dependent_voucher_type": self.doctype,
									"dependent_voucher_no": self.name,
									"dependent_voucher_detail_no": dep_row.name,
									"dependency_type": "Amount",
									"dependency_percentage": d.cost_percentage
								})

						for dep_row in self.get("consumed_materials"):
							sle.dependencies.append({
								"dependent_voucher_type": "Stock Entry",
								"dependent_voucher_no": dep_row.stock_entry,
								"dependent_voucher_detail_no": dep_row.ste_detail,
								"dependency_type": "Amount",
								"dependency_percentage": d.cost_percentage * dep_row.cost_percentage / 100
							})

				sl_entries.append(sle)

		# On cancellation, make stock ledger entry for
		# target warehouse first, to update serial no values properly

			# if cstr(d.s_warehouse) and self.docstatus == 2:
			# 	sl_entries.append(self.get_sl_entries(d, {
			# 		"warehouse": cstr(d.s_warehouse),
			# 		"actual_qty": -flt(d.stock_qty),
			# 		"incoming_rate": 0
			# 	}))

		if self.docstatus == 2:
			sl_entries.reverse()

		self.make_sl_entries(sl_entries, self.amended_from and 'Yes' or 'No',
			allow_negative_stock=allow_negative_stock)

	def get_gl_entries(self):
		gl_entries = super(StockEntry, self).get_gl_entries()

		total_basic_amount = sum([flt(t.basic_amount) for t in self.get("items") if t.t_warehouse])
		divide_based_on = total_basic_amount

		if self.get("additional_costs") and not total_basic_amount:
			# if total_basic_amount is 0, distribute additional charges based on qty
			divide_based_on = sum(item.qty for item in list(self.get("items")))

		item_account_wise_additional_cost = {}

		for t in self.get("additional_costs"):
			for d in self.get("items"):
				if d.t_warehouse:
					item_account_wise_additional_cost.setdefault((d.item_code, d.name), {})
					item_account_wise_additional_cost[(d.item_code, d.name)].setdefault(t.expense_account, 0.0)

					multiply_based_on = d.basic_amount if total_basic_amount else d.qty

					item_account_wise_additional_cost[(d.item_code, d.name)][t.expense_account] += \
						(t.amount * multiply_based_on) / divide_based_on

		if item_account_wise_additional_cost:
			for d in self.get("items"):
				for account, amount in (item_account_wise_additional_cost.get((d.item_code, d.name), {})).items():
					if not amount:
						continue

					gl_entries.append(self.get_gl_dict({
						"account": account,
						"against": d.expense_account,
						"cost_center": d.cost_center,
						"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
						"credit": amount
					}, item=d))

					gl_entries.append(self.get_gl_dict({
						"account": d.expense_account,
						"against": account,
						"cost_center": d.cost_center,
						"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
						"credit": -1 * amount # put it as negative credit instead of debit purposefully
					}, item=d))

		if self.get('cost_center'):
			for gle in gl_entries:
				gle.cost_center = self.get('cost_center')

		return gl_entries

	def update_work_order(self):
		def _validate_work_order(pro_doc):
			if flt(pro_doc.docstatus) != 1:
				frappe.throw(_("Work Order {0} must be submitted").format(self.work_order))

			if pro_doc.status == 'Stopped':
				frappe.throw(_("Transaction not allowed against stopped Work Order {0}").format(self.work_order))

		if self.job_card and self.purpose == "Material Transfer for Manufacture":
			job_doc = frappe.get_doc('Job Card', self.job_card)
			job_doc.set_transferred_qty(update_status=True)

		if self.work_order:
			self.pro_doc = frappe.get_doc("Work Order", self.work_order)
			_validate_work_order(self.pro_doc)
			self.pro_doc.run_method("update_status", from_doctype=self.doctype)
			self.pro_doc.notify_update()

		# Rejected / Loss Qty
		if self.purpose in ("Material Issue", "Material Transfer"):
			work_orders = list(set([d.work_order for d in self.items if d.get("work_order")]))
			for name in work_orders:
				work_order = frappe.get_doc("Work Order", name)
				work_order.set_packing_status(update=True)
				work_order.validate_overpacking(from_doctype=self.doctype)
				work_order.notify_update()

	@frappe.whitelist()
	def get_item_details(self, args=None, for_update=False):
		if isinstance(args, str):
			args = json.loads(args)

		args = frappe._dict(args)

		if not args.get('item_code'):
			frappe.throw(_("Item Code not provided"))

		item = frappe.get_cached_doc("Item", args.get('item_code'))

		out = frappe._dict({
			'uom': item.stock_uom,
			'stock_uom': item.stock_uom,
			'description': cstr(item.description).strip(),
			'image': item.image,
			'item_name': item.item_name,
			'hide_item_code': get_hide_item_code(item, args),
			'cost_center': get_default_cost_center(item, args),
			'qty': flt(args.get("qty")) or 0,
			'stock_qty': flt(args.get('qty')) or 0,
			'conversion_factor': 1,
			'batch_no': '',
			'actual_qty': 0,
			'basic_rate': 0,
			'serial_no': '',
			'has_serial_no': item.has_serial_no,
			'is_vehicle': item.is_vehicle,
			'has_batch_no': item.has_batch_no,
			'sample_quantity': item.sample_quantity,
			'allow_zero_valuation_rate': item.is_customer_provided_item,
			'has_alternative_item': has_alternative_item(item.name),
		})

		# update uom
		if args.get("uom"):
			uom_details = get_uom_details(args.get('item_code'), args.get('uom'), args.get('qty'))
			if not uom_details.get('not_convertible'):
				out['uom'] = args.get("uom")
				out.update(uom_details)

		company_copy_fields = {
			'stock_adjustment_account': 'expense_account',
			'cost_center': 'cost_center'
		}
		for company_field, field in company_copy_fields.items():
			if not out.get(field):
				out[field] = frappe.get_cached_value('Company',  self.company,  company_field)

		out['expense_account'] = get_expense_account(self.company, stock_entry_type=self.stock_entry_type,
			is_opening=self.is_opening, expense_account=out.get('expense_account'))

		args['posting_date'] = self.posting_date
		args['posting_time'] = self.posting_time

		stock_and_rate = get_warehouse_details(args) if args.get('warehouse') else {}
		out.update(stock_and_rate)

		# Contents UOM
		out.alt_uom = item.alt_uom
		out.alt_uom_size = item.alt_uom_size if item.alt_uom else 1.0
		out.alt_uom_qty = flt(out.stock_qty) * flt(out.alt_uom_size)

		if args.get("subcontracted_item"):
			out.subcontracted_item_name = frappe.get_cached_value("Item", args.get("subcontracted_item"), "item_name")
		elif self.purpose == "Send to Subcontractor" and self.get("purchase_order") and args.get("item_code"):
			from erpnext.buying.doctype.purchase_order.purchase_order import get_subcontracted_item_from_material_item
			out.update(get_subcontracted_item_from_material_item(args.get("item_code"), self.purchase_order))

		frappe.utils.call_hook_method("stock_entry_get_item_details", args, out)

		return out

	@frappe.whitelist()
	def set_item_cost_centers(self, row=None):
		for d in self.get("items"):
			if d.get("item_code") and (not row or d.name == row):
				d.cost_center = get_default_cost_center(d.item_code, {
					"doctype": "Stock Entry",
					"company": self.company,
					"project": d.get('project') or self.get('project'),
					"cost_center": d.get('cost_center'),
					"customer": self.get("customer"),
				})

	@frappe.whitelist()
	def set_items_for_stock_in(self):
		self.items = []

		if self.outgoing_stock_entry and self.purpose == 'Receive at Warehouse':
			doc = frappe.get_doc('Stock Entry', self.outgoing_stock_entry)

			if doc.per_transferred == 100:
				frappe.throw(_("Goods are already received against the outward entry {0}")
					.format(doc.name))

			for d in doc.items:
				self.append('items', {
					's_warehouse': d.t_warehouse,
					'item_code': d.item_code,
					'qty': d.qty,
					'uom': d.uom,
					'against_stock_entry': d.parent,
					'ste_detail': d.name,
					'stock_uom': d.stock_uom,
					'conversion_factor': d.conversion_factor,
					'serial_no': d.serial_no,
					'batch_no': d.batch_no
				})

	@frappe.whitelist()
	def get_items(self, auto_select_batches=False):
		auto_select_batches = cint(auto_select_batches)

		self.set('items', [])
		self.validate_work_order()

		if not self.posting_date or not self.posting_time:
			frappe.throw(_("Posting Date and Posting Time is mandatory"))

		self.set_work_order_details()

		if self.bom_no:
			allowed_raw_material_purposes = (
				"Material Issue", "Material Transfer", "Manufacture", "Repack",
				"Send to Subcontractor", "Material Transfer for Manufacture", "Material Consumption for Manufacture"
			)
			if self.purpose in allowed_raw_material_purposes:
				self.add_raw_material_items(auto_select_batches=auto_select_batches)

			if self.purpose in ("Manufacture", "Repack"):
				add_additional_cost(self, self.pro_doc)
				self.add_finished_goods_items_from_bom()
				self.add_scrap_items()

		self.set_missing_values()
		self.set_actual_qty()
		self.calculate_rate_and_amount(raise_error_if_no_rate=False)

	def add_raw_material_items(self, auto_select_batches=False):
		self.get_work_order()

		backflush_based_on = frappe.get_cached_value("Manufacturing Settings", None,
			"backflush_raw_materials_based_on")

		if self.work_order and self.purpose == "Manufacture":
			self.set_raw_materials_consumed_for_manufacture()

		if self.work_order and self.purpose == "Material Transfer for Manufacture":
			items_dict = self.get_raw_materials_to_transfer()
			self.add_to_stock_entry_detail(items_dict)

		elif (
			self.work_order
			and self.purpose in ("Manufacture", "Material Consumption for Manufacture")
			and not self.pro_doc.skip_transfer
			and backflush_based_on == "Material Transferred for Manufacture"
		):
			self.get_raw_materials_to_backflush_based_on_transfer()
			auto_select_batches = False

		else:
			items_dict = self.get_raw_materials_to_backflush_based_on_bom()
			self.add_to_stock_entry_detail(items_dict)

		if auto_select_batches:
			exclude_item_codes = [d.item_code for d in self.get("items") if d.get("item_code") and d.get("batch_no")]
			self.auto_select_batches(postprocess=False, exclude_item_codes=exclude_item_codes)

	def set_raw_materials_consumed_for_manufacture(self):
		self.consumed_materials = []

		if not self.work_order or self.purpose != "Manufacture":
			return

		consumption_data = frappe.db.sql("""
			select ste.name as stock_entry, i.name as ste_detail, ste.fg_completed_qty,
				i.item_code as material_item_code, i.item_name as material_item_name,
				i.qty as total_qty, i.uom, i.amount, i.valuation_rate
			from `tabStock Entry Detail` i
			inner join `tabStock Entry` ste on ste.name = i.parent
			where ste.docstatus = 1 and ste.purpose = 'Material Consumption for Manufacture' and ste.work_order = %s
				and ifnull(i.s_warehouse, '') != ''
			order by ste.posting_date, ste.posting_time, i.idx
		""", self.work_order, as_dict=1)

		if not consumption_data:
			return

		backflushed_map = dict(frappe.db.sql("""
			select cm.ste_detail, sum(cm.qty)
			from `tabStock Entry Consumed Material` cm
			inner join `tabStock Entry` ste on ste.name = cm.parent
			where ste.docstatus = 1 and ste.work_order = %s
			group by cm.ste_detail
		""", self.work_order))

		to_consume = []
		remaining_fg_completed_qty = {d.material_item_code: self.fg_completed_qty for d in consumption_data}

		for d in consumption_data:
			remaining_qty = d.total_qty - flt(backflushed_map.get(d.ste_detail))
			remaining_qty = flt(remaining_qty, 6)

			consumption_qty = d.total_qty * (remaining_fg_completed_qty[d.material_item_code] / d.fg_completed_qty)
			consumption_qty = flt(consumption_qty, 6)

			consumption_qty = min(consumption_qty, remaining_qty)
			if consumption_qty <= 0:
				continue

			d.qty = consumption_qty

			d.cost_percentage = d.qty / d.total_qty * 100
			d.amount = flt(d.amount * d.cost_percentage / 100, self.precision("amount", "items"))

			to_consume.append(d)

			current_fg_completed_qty = d.fg_completed_qty * d.cost_percentage / 100
			remaining_fg_completed_qty[d.material_item_code] -= current_fg_completed_qty

		for d in to_consume:
			self.append("consumed_materials", d)

	def get_raw_materials_to_backflush_based_on_transfer(self):
		self.get_work_order()

		transferred_qty = flt(self.pro_doc.material_transferred_for_manufacturing)
		completed_qty = flt(self.pro_doc.produced_qty) + flt(self.pro_doc.process_loss_qty)
		remaining_qty = max(transferred_qty - completed_qty, 0)

		if not self.fg_completed_qty:
			frappe.throw(_("Production Quantity is mandatory"))
		if not transferred_qty:
			frappe.throw(_("Work Order does not have transferred materials"))
		if not remaining_qty:
			frappe.throw(_("Work Order does not have remaining materials"))

		fg_total_qty = flt(self.fg_completed_qty) + flt(self.process_loss_qty)
		completed_to_remaining_ratio = fg_total_qty / remaining_qty

		transferred_materials_data = frappe.db.sql("""
			select
				i.item_code, i.t_warehouse as warehouse, ifnull(i.batch_no, '') as batch_no,
				sum(i.qty) as transferred_qty,
				i.item_name, i.original_item, i.description,
				i.uom, sum(i.qty * i.conversion_factor) / sum(i.qty) as conversion_factor,
				i.expense_account, i.cost_center
			from `tabStock Entry Detail` i
			inner join `tabStock Entry` ste on ste.name = i.parent
			where ste.docstatus = 1
				and ste.work_order = %s
				and ste.purpose = 'Material Transfer for Manufacture'
				and ifnull(i.t_warehouse, '') != ''
			group by i.item_code, i.t_warehouse, ifnull(i.batch_no, '')
		""", self.work_order, as_dict=1)

		backflushed_materials_data = frappe.db.sql("""
			select
				i.item_code, i.s_warehouse as warehouse, ifnull(i.batch_no, '') as batch_no,
				sum(qty) as qty, i.uom
			from `tabStock Entry Detail` i
			inner join `tabStock Entry` ste on ste.name = i.parent
			where ste.docstatus = 1
				and ste.work_order = %s
				and ste.purpose in ('Manufacture', 'Material Consumption for Manufacture')
				and ifnull(i.s_warehouse, '') != ''
			group by i.item_code, i.s_warehouse, ifnull(i.batch_no, '')
		""", self.work_order, as_dict=1)

		# calculate unconsumed / pending qty
		pending_materials = {}
		for d in transferred_materials_data:
			d.pending_qty = d.transferred_qty
			d.consumed_qty = 0

			pending_item_dict = pending_materials.setdefault(d.item_code, frappe._dict({
				"total_transferred_qty": 0, "total_pending_qty": 0, "total_consumed_qty": 0, "warehouse_batch_map": {}
			}))

			pending_item_dict.total_transferred_qty += d.transferred_qty
			pending_item_dict.total_pending_qty += d.pending_qty

			pending_item_dict.warehouse_batch_map.setdefault((d.warehouse, d.batch_no), d)

		for d in backflushed_materials_data:
			pending_item_dict = pending_materials.get(d.item_code)
			if pending_item_dict:
				pending_item_dict.total_consumed_qty += d.qty
				pending_item_dict.total_pending_qty -= d.qty

				pending_iwb = pending_item_dict.warehouse_batch_map.get((d.warehouse, d.batch_no))
				if pending_iwb:
					pending_iwb.consumed_qty += d.qty
					pending_iwb.pending_qty -= d.qty

		# calculate to consume qty
		qty_precision = frappe.get_precision("Stock Entry Detail", "qty")

		allowed_item_codes = None
		if self.purpose == "Material Consumption for Manufacture" and self.job_card:
			allowed_item_codes = self.get_job_card_item_codes()

		for item_code, pending_item_dict in pending_materials.items():
			if allowed_item_codes and item_code not in allowed_item_codes:
				continue

			total_pending_qty = flt(pending_item_dict.total_pending_qty, qty_precision)
			if total_pending_qty <= 0:
				continue

			total_required_qty = pending_item_dict.total_pending_qty * completed_to_remaining_ratio
			total_required_qty = flt(total_required_qty, qty_precision)

			total_to_consume = min(total_required_qty, total_pending_qty)

			# adjust for material consumptions
			if (
				self.purpose == "Manufacture"
				and pending_item_dict.total_consumed_qty
				and self.pro_doc.allow_material_consumption
			):
				material_per_fg = flt(pending_item_dict.total_transferred_qty / transferred_qty)
				expected_consumed_qty = completed_qty * material_per_fg
				excess_consumption = flt(pending_item_dict.total_consumed_qty) - expected_consumed_qty
				if flt(excess_consumption, qty_precision) > 0:
					total_to_consume -= excess_consumption

			total_remaining = total_to_consume

			for pending_iwb in pending_item_dict.warehouse_batch_map.values():
				if flt(total_remaining, qty_precision) <= 0:
					break

				pending_qty = flt(pending_iwb.pending_qty, qty_precision)
				if pending_qty <= 0:
					continue

				consumed_qty = min(pending_qty, total_remaining)

				self.add_to_stock_entry_detail({
					item_code: {
						"item_code": item_code,
						"item_name": pending_iwb.item_name,
						"description": pending_iwb.description,
						"batch_no": pending_iwb.batch_no,
						"from_warehouse": pending_iwb.warehouse,
						"to_warehouse": "",
						"qty": consumed_qty,
						"uom": pending_iwb.uom,
						"conversion_factor": pending_iwb.conversion_factor,
						"expense_account": pending_iwb.expense_account,
						"cost_center": pending_iwb.cost_center,
						"original_item": pending_iwb.original_item
					}
				})

				total_remaining -= consumed_qty
				pending_iwb.pending_qty -= consumed_qty

		# add skip transfer items from bom
		bom_materials = self.get_raw_materials_to_backflush_based_on_bom(
			ignore_process_loss=self.pro_doc.allow_material_consumption
		)
		bom_materials_skipped_transfer = {k: v for k, v in bom_materials.items() if v.get("skip_transfer_for_manufacture")}
		if bom_materials_skipped_transfer:
			self.add_to_stock_entry_detail(bom_materials_skipped_transfer)

	def get_raw_materials_to_backflush_based_on_bom(self, ignore_process_loss=False):
		if not self.fg_completed_qty:
			frappe.throw(_("Production Qty is mandatory"))

		self.get_work_order()

		fg_total_qty = flt(self.fg_completed_qty)
		if not ignore_process_loss:
			fg_total_qty += flt(self.process_loss_qty)

		items_dict = self.get_bom_raw_materials(fg_total_qty)
		wo_required_items_dict = self.pro_doc.get_required_items_dict() if self.pro_doc else frappe._dict()

		wo_alternative_items = {
			d.original_item: d.item_code
			for d in wo_required_items_dict.values()
			if d.original_item and d.original_item != d.item_code
		}

		if self.purpose == "Material Consumption for Manufacture" and self.job_card:
			allowed_item_codes = self.get_job_card_item_codes()
			items_dict = {item_code: item_dict for item_code, item_dict in items_dict.items() if item_code in allowed_item_codes}

		for item in items_dict.values():
			alternative_item_code = wo_alternative_items.get(item.item_code)
			alternative_required_item = wo_required_items_dict.get(alternative_item_code) if alternative_item_code else None

			if alternative_item_code and alternative_required_item:
				required_item_row = alternative_required_item
				self.modify_row_for_alternative_item(item, alternative_required_item)
			else:
				required_item_row = wo_required_items_dict.get(item.item_code)

			source_row = required_item_row or item

			# Set values from Work Order
			if self.pro_doc:
				item["skip_transfer_for_manufacture"] = source_row.skip_transfer_for_manufacture
				if (
					(self.pro_doc.from_wip_warehouse or not self.pro_doc.skip_transfer)
					and not source_row.skip_transfer_for_manufacture
				):
					item["from_warehouse"] = self.pro_doc.wip_warehouse
				else:
					item["from_warehouse"] = source_row.get('source_warehouse') or None

				item["batch_no"] = source_row.batch_no or None

				# adjust for material consumptions
				if (
					self.purpose == 'Manufacture'
					and required_item_row
					and flt(required_item_row.consumed_qty)
					and self.pro_doc.allow_material_consumption
				):
					completed_qty = flt(self.pro_doc.produced_qty)
					if not ignore_process_loss:
						completed_qty += flt(self.pro_doc.process_loss_qty)

					material_per_fg = flt(item.get("qty")) / fg_total_qty
					expected_consumed_qty = completed_qty * material_per_fg
					to_consume_qty = flt(item.get("qty"))
					excess_consumption = flt(required_item_row.consumed_qty) - expected_consumed_qty
					if flt(excess_consumption, self.precision("qty", "items")) > 0:
						item["qty"] = to_consume_qty - excess_consumption

			# Set Warehouse from Purchase Order
			if self.purchase_order and self.purpose == "Send to Subcontractor":
				purchase_order_warehouse = frappe._dict(frappe.db.sql("""
					select rm_item_code, reserve_warehouse
					from `tabPurchase Order` po, `tabPurchase Order Item Supplied` poitemsup
					where po.name = poitemsup.parent and po.name = %s
				""", self.purchase_order))

				item["from_warehouse"] = purchase_order_warehouse.get(item.item_code) or None

			item["to_warehouse"] = self.to_warehouse if self.purpose == "Send to Subcontractor" else ""

		if self.use_alternative_item:
			self.apply_available_alternative_items(items_dict)

		return items_dict

	def apply_available_alternative_items(self, items_dict):
		for item in items_dict.values():
			original_item_code = item.item_code
			warehouse = item.get("from_warehouse") or self.from_warehouse

			original_item_stock_qty = get_latest_stock_qty(original_item_code, warehouse)
			original_item_required_qty = flt(item.qty) * (flt(item.conversion_factor) or 1)
			if flt(original_item_stock_qty, 6) >= flt(original_item_required_qty, 6):
				continue

			available_alternative_items = get_available_alternative_items(item.item_code, warehouse, item.qty, item.uom)
			if len(available_alternative_items) == 1:
				original_item_code = item.get("item_code")
				alternative_item_code = available_alternative_items[0]
				self.modify_row_for_alternative_item(item, alternative_item_code)
				frappe.msgprint(
					_("Using Alternative Item {0} because {1} is not sufficiently available in Warehouse {2}").format(
						frappe.utils.get_link_to_form("Item", alternative_item_code),
						frappe.utils.get_link_to_form("Item", original_item_code),
						warehouse
					))
			elif len(available_alternative_items) > 1:
				frappe.msgprint(
					_("Could not determine Alternative Item of {0} because there are multiple alternative items available in {1}. Please select Alternative Item manually.").format(
						frappe.bold(original_item_code),
						warehouse
					), indicator="orange")
			else:
				pass

	def modify_row_for_alternative_item(self, row, alternative_item_code):
		alternative_item = frappe.get_cached_doc("Item", alternative_item_code)
		row["item_code"] = alternative_item_code
		row["item_name"] = alternative_item.item_name
		row["description"] = alternative_item.description
		row["uom"] = row.get("uom") or alternative_item.manufacture_uom or alternative_item.stock_uom
		row["conversion_factor"] = get_conversion_factor(alternative_item_code, row.get("uom")).get("conversion_factor") or 1

	def get_job_card_item_codes(self):
		if not self.job_card:
			return []

		if not self.get("_job_card_item_codes"):
			self._job_card_item_codes = {}
		if not self._job_card_item_codes.get(self.job_card):
			self._job_card_item_codes[self.job_card] = frappe.db.get_all("Job Card Item",
				filters={"parent": self.job_card}, pluck="item_code", distinct=True)

		return self._job_card_item_codes[self.job_card]

	def add_scrap_items(self):
		if self.purpose in ["Manufacture", "Repack"]:
			self.get_work_order()

			scrap_item_dict = self.get_bom_scrap_material(self.fg_completed_qty)
			for item in scrap_item_dict.values():
				item.idx = ''
				if self.pro_doc and self.pro_doc.scrap_warehouse:
					item["to_warehouse"] = self.pro_doc.scrap_warehouse

			self.add_to_stock_entry_detail(scrap_item_dict, bom_no=self.bom_no)

	def set_work_order_details(self):
		if not getattr(self, "pro_doc", None):
			self.pro_doc = frappe._dict()

		if self.work_order:
			# common validations
			self.get_work_order()

			if self.pro_doc:
				self.bom_no = self.pro_doc.bom_no
			else:
				# invalid work order
				self.work_order = None

	def add_finished_goods_items_from_bom(self):
		if self.work_order:
			self.get_work_order()
			item_code = self.pro_doc.production_item
			to_warehouse = self.pro_doc.wip_warehouse if self.pro_doc.produce_fg_in_wip_warehouse else self.pro_doc.fg_warehouse
		else:
			item_code = frappe.db.get_value("BOM", self.bom_no, "item")
			to_warehouse = self.to_warehouse

		item = frappe.get_cached_doc("Item", item_code)

		if not self.work_order and not to_warehouse:
			# in case of BOM
			to_warehouse = get_default_warehouse(item, {'company': self.company}, True)

		self.add_to_stock_entry_detail({
			item.name: {
				"to_warehouse": to_warehouse,
				"from_warehouse": "",
				"qty": self.fg_completed_qty,
				"item_name": item.item_name,
				"description": item.description,
				"stock_uom": item.stock_uom,
			}
		}, bom_no=self.bom_no)

	def get_bom_raw_materials(self, qty, process_loss_qty=0):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict

		self.get_work_order()

		# item dict = { item_code: {qty, description, stock_uom} }
		item_dict = get_bom_items_as_dict(self.bom_no, self.company, qty=qty + process_loss_qty,
			fetch_exploded=self.use_multi_level_bom, fetch_qty_in_stock_uom=False)

		used_alternative_items = get_used_alternative_items(work_order=self.work_order)
		for item in item_dict.values():
			item.from_warehouse = self.from_warehouse or item.source_warehouse or item.default_warehouse
			if item.item_code in used_alternative_items:
				alternative_item_data = used_alternative_items.get(item.item_code)
				item.item_code = alternative_item_data.item_code
				item.item_name = alternative_item_data.item_name
				item.stock_uom = alternative_item_data.stock_uom
				item.uom = alternative_item_data.uom
				item.conversion_factor = alternative_item_data.conversion_factor
				item.description = alternative_item_data.description

		return item_dict

	def get_bom_scrap_material(self, qty):
		from erpnext.manufacturing.doctype.bom.bom import get_bom_items_as_dict

		# item dict = { item_code: {qty, description, stock_uom} }
		item_dict = get_bom_items_as_dict(self.bom_no, self.company, qty=qty,
			fetch_exploded=0, fetch_scrap_items=1)

		for item in item_dict.values():
			item.from_warehouse = ""

		return item_dict

	def get_raw_materials_to_transfer(self):
		"""
			issue (item quantity) that is pending to issue or desire to transfer,
			whichever is less
		"""
		self.get_work_order()

		items_dict = self.get_work_order_required_items()
		qty_to_produce = flt(self.pro_doc.qty)

		for item, item_details in items_dict.items():
			if item_details.get("no_allowance"):
				required_qty_with_allowance = item_details.required_qty
			else:
				required_qty_with_allowance = self.pro_doc.get_qty_with_allowance(item_details.required_qty)

			pending_to_issue = required_qty_with_allowance - flt(item_details.transferred_qty)
			desire_to_transfer = flt(item_details.required_qty) * flt(self.fg_completed_qty) / qty_to_produce

			if desire_to_transfer <= pending_to_issue:
				items_dict[item]["qty"] = desire_to_transfer
			elif pending_to_issue > 0:
				items_dict[item]["qty"] = pending_to_issue
			else:
				items_dict[item]["qty"] = 0

		# delete items with 0 qty
		to_remove = []
		for item in items_dict.keys():
			if not items_dict[item]["qty"]:
				to_remove.append(item)

		for item in to_remove:
			del items_dict[item]

		# show some message
		if not len(items_dict):
			frappe.msgprint(_("""All items have already been transferred for this Work Order."""))

		return items_dict

	def get_work_order_required_items(self):
		self.get_work_order()

		items_dict = frappe._dict()

		if self.pro_doc.wip_warehouse and not frappe.db.get_value("Warehouse", self.pro_doc.wip_warehouse, "is_group", cache=1):
			wip_warehouse = self.pro_doc.wip_warehouse
		else:
			wip_warehouse = None

		for d in self.pro_doc.get("required_items"):
			required_qty_with_allowance = self.pro_doc.get_qty_with_allowance(d.required_qty)
			if flt(d.transferred_qty) >= required_qty_with_allowance:
				continue
			if d.skip_transfer_for_manufacture and self.purpose == "Material Transfer for Manufacture":
				continue

			item_row = d.as_dict()
			if d.source_warehouse and not frappe.db.get_value("Warehouse", d.source_warehouse, "is_group", cache=1):
				item_row["from_warehouse"] = d.source_warehouse

			item_row["to_warehouse"] = wip_warehouse

			items_dict.setdefault(d.item_code, item_row)

		return items_dict

	def add_to_stock_entry_detail(self, item_dict, bom_no=None):
		for item_code, item in item_dict.items():
			if flt(item["qty"], self.precision("qty", "items")) <= 0:
				continue

			stock_uom = item.get("stock_uom") or frappe.get_cached_value("Item", item_code, "stock_uom")

			row = self.append('items')
			row.item_code = item.get('item_code') or cstr(item_code)
			row.batch_no = item.get('batch_no')
			row.s_warehouse = item.get("from_warehouse")
			row.t_warehouse = item.get("to_warehouse")
			row.qty = flt(item["qty"], row.precision("qty"))
			row.uom = item["uom"] if item.get("uom") else stock_uom
			row.stock_uom = stock_uom

			row.expense_account = item.get("expense_account")
			row.cost_center = item.get("cost_center")
			row.subcontracted_item = item.get("subcontracted_item") or item.get("main_item_code")

			for field in ["purchase_order_item", "original_item", "description", "item_name"]:
				if item.get(field):
					row.set(field, item.get(field))

			if row.s_warehouse is None:
				row.s_warehouse = self.from_warehouse
			if row.t_warehouse is None:
				row.t_warehouse = self.to_warehouse

			# in stock uom
			row.conversion_factor = flt(item.get("conversion_factor")) or 1
			row.stock_qty = flt(item["qty"] * row.conversion_factor, 6)

			self.set_missing_item_values(row)

			# to be assigned for finished item
			row.bom_no = bom_no

	def validate_with_material_request(self):
		for item in self.get("items"):
			if item.material_request:
				mreq_item = frappe.db.get_value("Material Request Item",
					{"name": item.material_request_item, "parent": item.material_request},
					["item_code", "warehouse", "idx"], as_dict=True)
				if mreq_item.item_code != item.item_code or \
				mreq_item.warehouse != (item.s_warehouse if self.purpose== "Material Issue" else item.t_warehouse):
					frappe.throw(_("Item or Warehouse for row {0} does not match Material Request").format(item.idx),
						frappe.MappingMismatchError)

	def validate_packing_slips(self):
		if self.purpose not in ("Send to Subcontractor", "Material Issue", "Material Transfer"):
			if any(d for d in self.get("items") if d.get("packing_slip")):
				frappe.throw(_("Stock Entry against Packing Slip is not allowed for purpose '{0}'").format(self.purpose))

		super().validate_packing_slips()

	def validate_batch(self):
		if self.purpose in ["Material Transfer for Manufacture", "Manufacture", "Repack", "Send to Subcontractor"]:
			for item in self.get("items"):
				if item.batch_no:
					validate_batch_no(item.batch_no, item.item_code, transaction_date=self.posting_date)

	def update_purchase_order_supplied_items(self):
		if not self.purchase_order or self.purpose != "Send to Subcontractor":
			return

		po = frappe.get_doc("Purchase Order", self.purchase_order)
		po.set_raw_materials_supplied_qty(update=True)
		po.update_reserved_qty_for_subcontract()
		po.notify_update()

	def update_sales_order_in_serial_nos(self):
		if not self.work_order or self.purpose != "Manufacture":
			return

		so_name, item_code = frappe.db.get_value("Work Order", self.work_order, ["sales_order", "production_item"])
		if so_name and item_code:
			qty_to_reserve = get_reserved_qty_for_so(so_name, item_code)
			if qty_to_reserve:
				reserved_qty = frappe.db.sql("""select count(name) from `tabSerial No` where item_code=%s and
					sales_order=%s""", (item_code, so_name))
				if reserved_qty and reserved_qty[0][0]:
					qty_to_reserve -= reserved_qty[0][0]
				if qty_to_reserve > 0:
					for item in self.items:
						if item.item_code == item_code:
							serial_nos = (item.serial_no).split("\n")
							for serial_no in serial_nos:
								if qty_to_reserve > 0:
									frappe.db.set_value("Serial No", serial_no, "sales_order", so_name)
									qty_to_reserve -=1

	def validate_reserved_serial_no_consumption(self):
		for item in self.items:
			if item.s_warehouse and not item.t_warehouse and item.serial_no:
				for sr in get_serial_nos(item.serial_no):
					sales_order = frappe.db.get_value("Serial No", sr, "sales_order")
					if sales_order:
						frappe.throw(_("Item {0} (Serial No: {1}) cannot be consumed as is reserverd\
						 to fullfill Sales Order {2}.").format(item.item_code, sr, sales_order))

	def update_quality_inspection(self):
		if self.inspection_required:
			reference_type = reference_name = ''
			if self.docstatus == 1:
				reference_name = self.name
				reference_type = 'Stock Entry'

			for d in self.items:
				if d.quality_inspection:
					frappe.db.set_value("Quality Inspection", d.quality_inspection, {
						'reference_type': reference_type,
						'reference_name': reference_name
					})


@frappe.whitelist()
def move_sample_to_retention_warehouse(company, items):
	if isinstance(items, str):
		items = json.loads(items)
	retention_warehouse = frappe.db.get_single_value('Stock Settings', 'sample_retention_warehouse')
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = company
	stock_entry.purpose = "Material Transfer"
	stock_entry.set_stock_entry_type()
	for item in items:
		if item.get('sample_quantity') and item.get('batch_no'):
			sample_quantity = validate_sample_quantity(item.get('item_code'), item.get('sample_quantity'),
				item.get('stock_qty') or item.get('qty'), item.get('batch_no'))
			if sample_quantity:
				sample_serial_nos = ''
				if item.get('serial_no'):
					serial_nos = (item.get('serial_no')).split()
					if serial_nos and len(serial_nos) > item.get('sample_quantity'):
						serial_no_list = serial_nos[:-(len(serial_nos)-item.get('sample_quantity'))]
						sample_serial_nos = '\n'.join(serial_no_list)

				stock_entry.append("items", {
					"item_code": item.get('item_code'),
					"s_warehouse": item.get('t_warehouse'),
					"t_warehouse": retention_warehouse,
					"qty": item.get('sample_quantity'),
					"basic_rate": item.get('valuation_rate'),
					'uom': item.get('uom'),
					'stock_uom': item.get('stock_uom'),
					"conversion_factor": 1.0,
					"serial_no": sample_serial_nos,
					'batch_no': item.get('batch_no')
				})
	if stock_entry.get('items'):
		return stock_entry.as_dict()


@frappe.whitelist()
def make_stock_in_entry(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.purpose = 'Receive at Warehouse'
		target.set_stock_entry_type()

	def update_item(source_doc, target_doc, source_parent, target_parent):
		target_doc.t_warehouse = ''
		target_doc.s_warehouse = source_doc.t_warehouse
		target_doc.qty = source_doc.qty - source_doc.transferred_qty

	doclist = get_mapped_doc("Stock Entry", source_name, 	{
		"Stock Entry": {
			"doctype": "Stock Entry",
			"field_map": {
				"name": "outgoing_stock_entry"
			},
			"validation": {
				"docstatus": ["=", 1]
			}
		},
		"Stock Entry Detail": {
			"doctype": "Stock Entry Detail",
			"field_map": {
				"name": "ste_detail",
				"parent": "against_stock_entry",
				"serial_no": "serial_no",
				"batch_no": "batch_no"
			},
			"postprocess": update_item,
			"condition": lambda doc, source, target: flt(doc.qty) - flt(doc.transferred_qty) > 0.01
		},
	}, target_doc, set_missing_values)

	return doclist


@frappe.whitelist()
def get_work_order_details(work_order, purpose=None):
	work_order = frappe.get_doc("Work Order", work_order)

	return {
		"from_bom": 1,
		"bom_no": work_order.bom_no,
		"use_multi_level_bom": work_order.use_multi_level_bom,
		"wip_warehouse": work_order.wip_warehouse,
		"fg_warehouse": work_order.fg_warehouse,
		"fg_completed_qty": work_order.get_balance_qty(purpose)
	}


def get_operating_cost_per_unit(work_order=None, bom_no=None):
	operating_cost_per_unit = 0
	if work_order:
		for d in work_order.get("operations"):
			if flt(d.completed_qty):
				operating_cost_per_unit += flt(d.actual_operating_cost) / flt(d.completed_qty)
			elif work_order.qty:
				operating_cost_per_unit += flt(d.planned_operating_cost) / flt(work_order.qty)
	elif bom_no:
		bom = frappe.db.get_value("BOM", bom_no, ["operating_cost", "quantity"], as_dict=1)
		if bom.quantity:
			operating_cost_per_unit = flt(bom.operating_cost) / flt(bom.quantity)

	return operating_cost_per_unit


def get_additional_operating_costs(work_order=None, bom_no=None, use_multi_level_bom=0):
	from erpnext.manufacturing.doctype.bom.bom import get_additional_operating_cost_per_unit

	additional_costs = []

	if work_order:
		additional_costs = work_order.get("additional_costs")
	elif bom_no:
		additional_costs = get_additional_operating_cost_per_unit(bom_no, use_multi_level_bom)

	return additional_costs


def get_used_alternative_items(purchase_order=None, work_order=None):
	cond = ""

	if purchase_order:
		cond = "and ste.purpose = 'Send to Subcontractor' and ste.purchase_order = '{0}'".format(purchase_order)
	elif work_order:
		cond = "and ste.purpose = 'Material Transfer for Manufacture' and ste.work_order = '{0}'".format(work_order)

	if not cond: return {}

	used_alternative_items = {}
	data = frappe.db.sql("""
		select
			sted.item_code, sted.item_name, sted.original_item,
			sted.uom, sted.conversion_factor, sted.stock_uom, sted.description
		from
			`tabStock Entry` ste, `tabStock Entry Detail` sted
		where
			sted.parent = ste.name and ste.docstatus = 1 and sted.original_item !=  sted.item_code
			{0} """.format(cond), as_dict=1)

	for d in data:
		used_alternative_items[d.original_item] = d

	return used_alternative_items


@frappe.whitelist()
def get_uom_details(item_code, uom, qty):
	"""Returns dict `{"conversion_factor": [value], "stock_qty": qty * [value]}`

	:param args: dict with `item_code`, `uom` and `qty`"""
	conversion = get_conversion_factor(item_code, uom)
	conversion_factor = flt(conversion.get("conversion_factor"))
	not_convertible = cint(conversion.get('not_convertible'))

	if not conversion_factor:
		frappe.msgprint(_("UOM coversion factor required for UOM: {0} in Item: {1}")
			.format(uom, item_code))
		ret = {'uom': ''}
	else:
		ret = {
			'conversion_factor': flt(conversion_factor),
			'stock_qty': flt(qty) * flt(conversion_factor),
			'not_convertible': not_convertible
		}
	return ret


@frappe.whitelist()
def get_expired_batch_items():
	return frappe.db.sql("""select b.item, sum(sle.actual_qty) as qty, sle.batch_no, sle.warehouse, sle.stock_uom\
	from `tabBatch` b, `tabStock Ledger Entry` sle
	where b.expiry_date <= %s
	and b.expiry_date is not NULL
	and b.batch_id = sle.batch_no
	group by sle.warehouse, sle.item_code, sle.batch_no""",(nowdate()), as_dict=1)


@frappe.whitelist()
def get_warehouse_details(args):
	if isinstance(args, str):
		args = json.loads(args)

	args = frappe._dict(args)

	ret = {}
	if args.warehouse and args.item_code:
		args.update({
			"posting_date": args.posting_date,
			"posting_time": args.posting_time,
		})
		ret = {
			"actual_qty" : get_previous_sle(args).get("qty_after_transaction") or 0,
			"basic_rate" : get_incoming_rate(args) if not args.get('customer_provided') else 0
		}
	return ret


@frappe.whitelist()
def validate_sample_quantity(item_code, sample_quantity, qty, batch_no = None):
	if cint(qty) < cint(sample_quantity):
		frappe.throw(_("Sample quantity {0} cannot be more than received quantity {1}").format(sample_quantity, qty))
	retention_warehouse = frappe.db.get_single_value('Stock Settings', 'sample_retention_warehouse')
	retainted_qty = 0
	if batch_no:
		retainted_qty = get_batch_qty(batch_no, retention_warehouse, item_code)
	max_retain_qty = frappe.get_value('Item', item_code, 'sample_quantity')
	if retainted_qty >= max_retain_qty:
		frappe.msgprint(_("Maximum Samples - {0} have already been retained for Batch {1} and Item {2} in Batch {3}.").
			format(retainted_qty, batch_no, item_code, batch_no), alert=True)
		sample_quantity = 0
	qty_diff = max_retain_qty-retainted_qty
	if cint(sample_quantity) > cint(qty_diff):
		frappe.msgprint(_("Maximum Samples - {0} can be retained for Batch {1} and Item {2}.").
			format(max_retain_qty, batch_no, item_code), alert=True)
		sample_quantity = qty_diff
	return sample_quantity


@frappe.whitelist()
def get_expense_account(company, stock_entry_type=None, is_opening='No', expense_account=None):
	if company and is_opening == 'Yes':
		return frappe.get_cached_value('Company', company, 'temporary_opening_account')

	if stock_entry_type:
		account = frappe.get_cached_value('Stock Entry Type', stock_entry_type, 'expense_account')
		if account:
			return account

	if frappe.get_cached_value('Company', company, 'stock_adjustment_account'):
		return frappe.get_cached_value('Company', company, 'stock_adjustment_account')

	return expense_account


@frappe.whitelist()
def get_item_expense_accounts(args):
	if isinstance(args, str):
		args = json.loads(args)

	args = frappe._dict(args)

	expense_account_item = {}
	for d in args.get('items'):
		account = get_expense_account(company=args.company, stock_entry_type=args.stock_entry_type,
			is_opening=args.is_opening, expense_account=d.get('expense_account'))
		expense_account_item[d.get("name")] = account

	return expense_account_item
