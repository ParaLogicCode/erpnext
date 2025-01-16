# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate, cint, nowdate, get_link_to_form, round_up
from erpnext.manufacturing.doctype.bom.bom import validate_bom_no, get_bom_items_as_dict
from erpnext.stock.doctype.item.item import validate_end_of_life
from erpnext.stock.stock_balance import get_planned_qty, update_bin_qty
from erpnext.stock.utils import get_bin, validate_warehouse_company, get_latest_stock_qty
from erpnext.utilities.transaction_base import validate_uom_is_integer
from erpnext.controllers.status_updater import StatusUpdaterERP
from erpnext.stock.get_item_details import get_default_bom
from frappe.model.mapper import get_mapped_doc
import json
import math
import copy


class OverProductionError(frappe.ValidationError): pass
class StockOverProductionError(frappe.ValidationError): pass
class OperationTooLongError(frappe.ValidationError): pass
class ItemHasVariantError(frappe.ValidationError): pass


form_grid_templates = {
	"operations": "templates/form_grid/work_order_grid.html"
}


class WorkOrder(StatusUpdaterERP):
	def get_feed(self):
		return "{0} {1} of {2}".format(
			frappe.format(self.get_formatted('qty')), self.get('stock_uom'), self.get('item_name') or self.get('production_item')
		)

	def onload(self):
		ms = frappe.get_cached_doc("Manufacturing Settings", None)
		self.set_onload("backflush_raw_materials_based_on", ms.backflush_raw_materials_based_on)

		self.set_available_qty()

	def validate(self):
		self.validate_qty()
		self.validate_operation_time()
		self.validate_production_item()
		self.validate_bom()
		self.validate_sales_order()
		self.set_default_warehouse()

		self.set_required_items(reset_only_qty=bool(len(self.get("required_items"))))
		self.validate_warehouses()
		self.validate_batch_nos()

		ste_qty_map = self.get_ste_qty_map()
		self.set_subcontracting_status()
		self.set_production_status(ste_qty_map=ste_qty_map)
		self.set_operation_status()
		self.set_actual_dates(ste_qty_map=ste_qty_map)
		self.set_completed_qty()
		self.set_packing_status()
		self.set_required_items_status()

		self.calculate_raw_material_cost()
		self.calculate_operating_cost()
		self.calculate_total_cost()

		self.set_status()

	def on_submit(self):
		self.update_sales_order_status()
		self.update_material_request_ordered_qty()
		self.update_production_plan_ordered_qty()
		self.update_reserved_qty_for_production()
		self.update_planned_qty()

	def on_cancel(self):
		self.validate_cancel()
		self.update_status_on_cancel()
		self.update_sales_order_status()
		self.update_material_request_ordered_qty()
		self.update_production_plan_ordered_qty()
		self.update_reserved_qty_for_production()
		self.update_planned_qty()

		self.delete_job_card()

	def validate_qty(self):
		self.qty = flt(self.qty, self.precision("qty"))
		if self.qty <= 0:
			frappe.throw(_("Quantity to Produce must be greater than 0."))

		validate_uom_is_integer(self, "stock_uom", ["qty"])

	def validate_operation_time(self):
		for d in self.operations:
			if d.time_in_mins < 0:
				frappe.throw(_("Operation Time cannot be a negative number for Operation {0}".format(d.operation)))

	def validate_production_item(self):
		if self.production_item:
			item = frappe.get_cached_value("Item", self.production_item, ['has_variants', 'end_of_life', 'disabled'], as_dict=1)
			validate_end_of_life(self.production_item, end_of_life=item.end_of_life, disabled=item.disabled)
			if item.has_variants:
				frappe.throw(_("Work Order cannot be raised against an Item Template"), ItemHasVariantError)

	def validate_bom(self):
		if self.get("bom_no"):
			validate_bom_no(self.production_item, self.bom_no)

	def validate_sales_order(self):
		if not self.sales_order:
			return

		self.check_sales_order_on_hold_or_close()

		sales_order_customer = frappe.db.get_value("Sales Order", self.sales_order, "customer", cache=1)
		if sales_order_customer and not self.customer:
			self.customer = sales_order_customer

		if self.customer and self.customer != sales_order_customer:
			frappe.throw(_("Customer does not match with {0}").format(
				frappe.get_desk_link("Sales Order", self.sales_order))
			)

		so = frappe.db.sql("""
			select so.name, so_item.delivery_date, so.project
			from `tabSales Order` so
			inner join `tabSales Order Item` so_item on so_item.parent = so.name
			left join `tabProduct Bundle Item` pk_item on so_item.item_code = pk_item.parent
			where so.name = %s
				and so.docstatus = 1
				and (so_item.item_code = %s or pk_item.item_code = %s)
		""", (self.sales_order, self.production_item, self.production_item), as_dict=1)

		if not so:
			so = frappe.db.sql("""
				select so.name, so_item.delivery_date, so.project
				from `tabSales Order` so, `tabSales Order Item` so_item, `tabPacked Item` packed_item
				where so.name = %s
					and so.name = so_item.parent
					and so.name = packed_item.parent
					and so_item.item_code = packed_item.parent_item
					and so.docstatus = 1
					and packed_item.item_code=%s
			""", (self.sales_order, self.production_item), as_dict=1)

		if len(so):
			if not self.expected_delivery_date:
				self.expected_delivery_date = so[0].delivery_date

			if so[0].project:
				self.project = so[0].project

			if not self.material_request:
				self.validate_sales_order_qty()
		else:
			frappe.throw(_("Sales Order {0} is not valid").format(self.sales_order))

	def check_sales_order_on_hold_or_close(self):
		if not self.sales_order:
			return

		status = frappe.db.get_value("Sales Order", self.sales_order, "status", cache=1)
		if status in ("Closed", "On Hold"):
			frappe.throw(_("{0} is {1}").format(frappe.get_desk_link("Sales Order", self.sales_order), status))

	def validate_sales_order_qty(self):
		# already ordered qty
		ordered_qty_against_so = frappe.db.sql("""
			select sum(qty)
			from `tabWork Order`
			where production_item = %s and sales_order = %s and docstatus < 2 and name != %s
		""", (self.production_item, self.sales_order, self.name))[0][0]

		total_qty = flt(flt(ordered_qty_against_so) + flt(self.qty), self.precision('qty'))

		# get qty from Sales Order Item table
		so_item_qty = frappe.db.sql("""
			select sum(stock_qty)
			from `tabSales Order Item`
			where parent = %s and item_code = %s
		""", (self.sales_order, self.production_item))[0][0]

		# get qty from Packing Item table
		dnpi_qty = frappe.db.sql("""
			select sum(qty)
			from `tabPacked Item`
			where parent = %s and parenttype = 'Sales Order' and item_code = %s
		""", (self.sales_order, self.production_item))[0][0]

		# total qty in SO
		so_qty = round_up(flt(so_item_qty) + flt(dnpi_qty), self.precision('qty'))

		allowance_percentage = flt(frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_sales_order"))

		if total_qty > so_qty + (allowance_percentage/100 * so_qty):
			frappe.throw(_("Cannot produce more Item {0} than Sales Order quantity {1}").format(
				frappe.bold(self.production_item), frappe.format(so_qty)
			), OverProductionError)

	def set_default_warehouse(self):
		warehouse_map = get_default_warehouse()

		for field, warehouse in warehouse_map.items():
			if not self.get(field) and warehouse:
				self.set(field, warehouse)

	def validate_warehouses(self):
		if self.docstatus == 1:
			if (
				not self.wip_warehouse
				and (not self.skip_transfer or self.from_wip_warehouse or self.produce_fg_in_wip_warehouse)
			):
				frappe.throw(_("Work-in-Progress Warehouse is required before Submit"))

			if not self.fg_warehouse:
				frappe.throw(_("Target Warehouse is required before Submit"))

			for d in self.get("required_items"):
				if not d.source_warehouse and self.source_warehouse:
					d.source_warehouse = self.source_warehouse
				if not d.source_warehouse:
					frappe.throw(_("Row #{0}: Source Warehouse is required before submit").format(d.idx))

		warehouses = [self.fg_warehouse, self.wip_warehouse]
		for d in self.get("required_items"):
			if d.source_warehouse not in warehouses:
				warehouses.append(d.source_warehouse)

		for wh in warehouses:
			if wh:
				validate_warehouse_company(wh, self.company)

	def validate_batch_nos(self):
		from erpnext.stock.doctype.batch.batch import validate_batch_no

		for d in self.get("required_items"):
			if not d.has_batch_no:
				d.batch_no = None

			if d.batch_no:
				validate_batch_no(d.batch_no, d.item_code, transaction_date=self.transaction_date)

	@frappe.whitelist()
	def get_items_and_operations_from_bom(self):
		self.set_required_items()
		self.calculate_raw_material_cost()
		self.set_work_order_operations()
		self.calculate_total_cost()

		return check_if_scrap_warehouse_mandatory(self.bom_no)

	def set_required_items(self, reset_only_qty=False):
		'''set required_items for production to keep track of reserved qty'''
		if not reset_only_qty:
			self.required_items = []

		if not self.bom_no or not self.qty:
			return

		item_dict = get_bom_items_as_dict(self.bom_no, self.company, qty=self.qty,
			fetch_exploded=self.use_multi_level_bom, fetch_qty_in_stock_uom=False)

		if reset_only_qty:
			for row in self.get("required_items"):
				item = item_dict.get(row.item_code)
				if not item and row.original_item:
					item = item_dict.get(row.original_item)

				if not item:
					continue

				row.total_qty = flt(item.qty)
				row.uom = item.uom
				row.stock_uom = item.stock_uom
				row.conversion_factor = flt(item.conversion_factor) or 1
				row.has_batch_no = cint(item.has_batch_no)

				row.required_qty = row.total_qty / self.qty * self.get_producible_qty()
				row.stock_required_qty = row.required_qty * row.conversion_factor
		else:
			for item in sorted(item_dict.values(), key=lambda d: d.get('idx') or 9999):
				row = self.append('required_items', {
					'operation': item.operation,
					'item_code': item.item_code,
					'item_name': item.item_name,
					'description': item.description,
					'total_qty': flt(item.qty),
					'uom': item.uom,
					'stock_uom': item.stock_uom,
					'conversion_factor': flt(item.conversion_factor) or 1,
					'has_batch_no': cint(item.has_batch_no),
					'source_warehouse': self.source_warehouse or item.source_warehouse or item.default_warehouse,
					'skip_transfer_for_manufacture': item.skip_transfer_for_manufacture
				})

				row.required_qty = row.total_qty / self.qty * self.get_producible_qty()
				row.stock_required_qty = row.required_qty * row.conversion_factor

				if not self.project:
					self.project = item.get("project")

		self.set_available_qty()

	def set_required_items_status(self, update=False, update_modified=True):
		item_ste_map = {}
		original_item_ste_map = {}

		if self.docstatus == 1:
			ste_data = frappe.db.sql("""
				select ste.purpose, i.item_code, i.original_item, i.qty * i.conversion_factor as stock_qty
				from `tabStock Entry` ste
				inner join `tabStock Entry Detail` i on i.parent = ste.name
				where ste.work_order = %s
					and ste.purpose in ('Manufacture', 'Material Transfer for Manufacture', 'Material Consumption for Manufacture')
					and ste.docstatus = 1
			""", self.name, as_dict=1)

			for d in ste_data:
				item_ste_map.setdefault(d.item_code, {}).setdefault(d.purpose, 0)
				item_ste_map[d.item_code][d.purpose] += d.stock_qty

				if d.original_item:
					original_item_ste_map.setdefault(d.original_item, {}).setdefault(d.purpose, 0)
					original_item_ste_map[d.original_item][d.purpose] += d.stock_qty

		for d in self.required_items:
			ste_qty_map = item_ste_map.get(d.item_code, {}) or original_item_ste_map.get(d.item_code, {})

			d.consumed_qty = flt(ste_qty_map.get("Manufacture")) + flt(ste_qty_map.get("Material Consumption for Manufacture"))
			d.transferred_qty = flt(ste_qty_map.get("Material Transfer for Manufacture"))

			if d.conversion_factor:
				d.consumed_qty /= d.conversion_factor
				d.transferred_qty /= d.conversion_factor

			if update:
				d.db_set({
					"consumed_qty": d.consumed_qty,
					"transferred_qty": d.transferred_qty,
				}, update_modified=update_modified)

	def set_work_order_operations(self):
		"""Fetch operations from BOM and set in 'Work Order'"""
		from erpnext.manufacturing.doctype.bom.bom import get_additional_operating_cost_per_unit

		self.set('operations', [])

		if not self.bom_no:
			return

		if self.use_multi_level_bom:
			bom_list = frappe.get_doc("BOM", self.bom_no).traverse_tree()
		else:
			bom_list = [self.bom_no]

		operations = []
		if bom_list:
			bom_list_order_str = ', '.join(frappe.db.escape(bom) for bom in bom_list)
			operations = frappe.db.sql("""
				SELECT operation, description, workstation, base_hour_rate AS hour_rate, time_in_mins,
					'Pending' AS status, parent AS bom, batch_size
				FROM `tabBOM Operation`
				WHERE parent IN %s
				ORDER BY FIELD(parent, {bom_list_order_str}), idx
			""".format(bom_list_order_str=bom_list_order_str), [bom_list], as_dict=1)

		self.set('operations', operations)

		if self.use_multi_level_bom and self.get('operations') and self.get('items'):
			raw_material_operations = [d.operation for d in self.get('items')]
			operations = [d.operation for d in self.get('operations')]

			for operation in raw_material_operations:
				if operation not in operations:
					self.append('operations', {
						'operation': operation
					})

		# Additional costs
		additional_costs = get_additional_operating_cost_per_unit(self.bom_no, self.use_multi_level_bom, bom_list=bom_list)
		if additional_costs:
			self.set('additional_costs', [])
			for d in additional_costs:
				self.append('additional_costs', d)

		self.calculate_time()

	def set_operation_status(self, update=False, update_modified=True):
		if not self.operations:
			return

		operation_data_map = {}

		if self.docstatus == 1:
			operation_data = frappe.db.sql("""
				SELECT operation_id,
					sum(total_time_in_mins) as time_in_mins,
					sum(total_completed_qty) as completed_qty,
					min(actual_start_dt) as start_time,
					max(actual_end_dt) as end_time
				FROM `tabJob Card`
				WHERE docstatus = 1 AND work_order = %s
				GROUP BY operation_id
			""", self.name, as_dict=1)

			for d in operation_data:
				operation_data_map.setdefault(d.operation_id, d)

		min_production_qty = flt(self.get_min_qty(self.producible_qty), self.precision("qty"))

		for d in self.operations:
			# set operation status
			d.completed_qty = flt(operation_data_map.get(d.name, {}).get('completed_qty'))

			if self.status == "Stopped" and d.completed_qty > 0:
				d.status = "Completed"
			elif not d.completed_qty:
				d.status = "Pending"
			elif flt(d.completed_qty) < min_production_qty and self.production_status != "Produced":
				d.status = "Work in Progress"
			else:
				d.status = "Completed"

			d.actual_operation_time = flt(operation_data_map.get(d.name, {}).get('time_in_mins'))
			d.actual_start_time = operation_data_map.get(d.name, {}).get('start_time')
			d.actual_end_time = operation_data_map.get(d.name, {}).get('end_time') if d.status == "Completed" else None

		self.calculate_operating_cost()

		if update:
			for row in self.operations:
				row.db_set({
					'completed_qty': row.completed_qty,
					'actual_start_time': row.actual_start_time,
					'actual_end_time': row.actual_end_time,
					'actual_operation_time': row.actual_operation_time,
					'actual_operating_cost': row.actual_operating_cost,
					'planned_operating_cost': row.planned_operating_cost,
					'status': row.status,
				}, update_modified=update_modified)

			for row in self.get('additional_costs'):
				row.db_set({
					'amount': row.amount
				}, update_modified=update_modified)

			self.db_set({
				'actual_operating_cost': self.actual_operating_cost,
				'planned_operating_cost': self.planned_operating_cost,
				'additional_operating_cost': self.additional_operating_cost,
				'total_operating_cost': self.total_operating_cost,
			}, update_modified=update_modified)

	def validate_completed_qty_in_operations(self, from_doctype=None):
		max_production_qty = flt(self.get_qty_with_allowance(self.producible_qty), self.precision("qty"))
		transferred_qty = flt(self.material_transferred_for_manufacturing, self.precision("qty"))

		for d in self.operations:
			completed_qty = flt(d.completed_qty, self.precision("qty"))

			if completed_qty > max_production_qty:
				frappe.throw(_("Completed Qty {0} {1} for Operation {2} cannot be greater than maximum quantity {3} {1} in {4}").format(
					frappe.bold(d.get_formatted("completed_qty")),
					self.stock_uom,
					frappe.bold(d.operation),
					frappe.bold(frappe.format(max_production_qty)),
					frappe.get_desk_link("Work Order", self.name)
				))

			if self.produced_qty > completed_qty:
				frappe.throw(_("Produced Qty {0} {1} cannot be greater than {2} Operation Completed Qty {3} {1} in {4}").format(
					frappe.bold(frappe.format(self.produced_qty)),
					self.stock_uom,
					frappe.bold(d.operation),
					frappe.bold(d.get_formatted("completed_qty")),
					frappe.get_desk_link("Work Order", self.name)
				))

			if not self.skip_transfer and completed_qty > transferred_qty:
				frappe.throw(_("Completed Qty {0} {1} for Operation {2} cannot be more than the Material Transferred for Manufacturing {3} {1} in {4}").format(
					frappe.bold(d.get_formatted("completed_qty")),
					self.stock_uom,
					frappe.bold(d.operation),
					frappe.bold(frappe.format(transferred_qty)),
					frappe.get_desk_link("Work Order", self.name)
				), StockOverProductionError)

	def calculate_time(self):
		bom_qty = frappe.db.get_value("BOM", self.bom_no, "quantity", cache=1)

		for d in self.get("operations"):
			d.time_in_mins = flt(d.time_in_mins) / flt(bom_qty) * math.ceil(flt(self.qty) / flt(d.batch_size))

		self.calculate_operating_cost()

	def calculate_raw_material_cost(self):
		bom_cost, bom_qty = frappe.db.get_value("BOM", self.bom_no, ["base_raw_material_cost", "quantity"])
		self.raw_material_cost = bom_cost * flt(self.qty) / bom_qty if bom_qty else 0
		self.total_raw_material_qty = sum([d.total_qty for d in self.required_items])

	def calculate_operating_cost(self):
		self.planned_operating_cost = 0.0
		self.actual_operating_cost = 0.0

		for d in self.get("operations"):
			d.planned_operating_cost = flt(d.hour_rate) * (flt(d.time_in_mins) / 60.0)
			d.actual_operating_cost = flt(d.hour_rate) * (flt(d.actual_operation_time) / 60.0)

			self.planned_operating_cost += flt(d.planned_operating_cost)
			self.actual_operating_cost += flt(d.actual_operating_cost)

		self.additional_operating_cost = 0.0
		for d in self.get('additional_costs'):
			d.amount = flt(flt(d.rate) * flt(self.qty), d.precision('amount'))
			self.additional_operating_cost += d.amount

		variable_cost = self.actual_operating_cost if self.actual_operating_cost else self.planned_operating_cost

		self.total_operating_cost = flt(self.additional_operating_cost) + flt(variable_cost)

	def calculate_total_cost(self):
		self.total_cost = self.raw_material_cost + self.total_operating_cost

	def set_available_qty(self):
		for d in self.get("required_items"):
			if d.source_warehouse:
				d.available_qty_at_source_warehouse = get_latest_stock_qty(d.item_code, d.source_warehouse)

			if self.wip_warehouse:
				d.available_qty_at_wip_warehouse = get_latest_stock_qty(d.item_code, self.wip_warehouse)

	def update_status(self, status=None, from_doctype=None):
		self.set_status(status=status)

		if from_doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice") or not from_doctype:
			self.set_subcontracting_status(update=True)
		if from_doctype == "Purchase Order":
			self.set_required_items(reset_only_qty=True)
			self.update_child_table("required_items")

		ste_qty_map = self.get_ste_qty_map()

		self.set_production_status(update=True, ste_qty_map=ste_qty_map)
		self.set_operation_status(update=True)
		self.set_actual_dates(update=True, ste_qty_map=ste_qty_map)
		self.set_completed_qty(update=True)
		self.set_packing_status(update=True)
		self.set_required_items_status(update=True)

		self.validate_overproduction(from_doctype)
		self.validate_completed_qty_in_operations(from_doctype)
		self.validate_overpacking(from_doctype)

		self.set_status(status=status, update=True)

		self.update_sales_order_status()
		self.update_production_plan_produced_qty()

		self.update_planned_qty()
		self.update_reserved_qty_for_production()

		return self.status

	def set_subcontracting_status(self, update=False, update_modified=True):
		self.subcontract_order_qty = 0
		self.subcontract_received_qty = 0

		if self.docstatus == 1:
			po_qty = frappe.db.sql("""
				select sum(stock_qty)
				from `tabPurchase Order Item`
				where docstatus = 1 and work_order = %s
			""", self.name)
			po_qty = flt(po_qty[0][0]) if po_qty else 0

			prec_qty = frappe.db.sql("""
				select sum(stock_qty)
				from `tabPurchase Receipt Item`
				where docstatus = 1 and work_order = %s
			""", self.name)
			prec_qty = flt(prec_qty[0][0]) if prec_qty else 0

			pinv_qty = frappe.db.sql("""
				select sum(i.stock_qty)
				from `tabPurchase Invoice Item` i
				inner join `tabPurchase Invoice` p on p.name = i.parent
				where p.docstatus = 1 and p.update_stock = 1 and i.work_order = %s
			""", self.name)
			pinv_qty = flt(pinv_qty[0][0]) if pinv_qty else 0

			self.subcontract_order_qty = po_qty
			self.subcontract_received_qty = prec_qty + pinv_qty

		self.per_subcontract_received = flt(self.subcontract_received_qty / self.subcontract_order_qty * 100
			if self.subcontract_order_qty else 0, 3)

		ordered_qty = flt(self.subcontract_order_qty, self.precision("subcontract_order_qty"))
		received_qty = flt(self.subcontract_received_qty, self.precision("subcontract_order_qty"))

		if received_qty and (received_qty >= ordered_qty or self.status == "Stopped"):
			self.subcontracting_status = "Received"
		elif self.status == "Stopped" or not ordered_qty:
			self.subcontracting_status = "Not Applicable"
		else:
			self.subcontracting_status = "To Receive"

		self.producible_qty = self.get_producible_qty()

		if update:
			self.db_set({
				"producible_qty": self.producible_qty,
				"subcontract_order_qty": self.subcontract_order_qty,
				"subcontract_received_qty": self.subcontract_received_qty,
				"per_subcontract_received": self.per_subcontract_received,
				"subcontracting_status": self.subcontracting_status,
			}, update_modified=update_modified)

	def set_production_status(self, update=False, update_modified=True, ste_qty_map=None):
		if not ste_qty_map:
			ste_qty_map = self.get_ste_qty_map()

		# Set completed qtys
		to_update = frappe._dict({
			"produced_qty": flt(ste_qty_map.get("Manufacture", {}).get("fg_completed_qty")),
			"process_loss_qty": flt(ste_qty_map.get("Manufacture", {}).get("process_loss_qty")),
			"material_transferred_for_manufacturing": flt(ste_qty_map.get("Material Transfer for Manufacture", {}).get("fg_completed_qty")),
		})
		if self.operations and self.transfer_material_against == 'Job Card':
			del to_update["material_transferred_for_manufacturing"]
		else:
			# Set percentage transferred
			to_update.per_material_transferred = flt(to_update.material_transferred_for_manufacturing / self.qty * 100, 3)

		# Set percentage produced
		to_update.per_produced = flt(to_update.produced_qty / self.qty * 100, 3)

		# Set status fields
		if self.docstatus == 1:
			production_completion_qty = flt(to_update.produced_qty + to_update.process_loss_qty, self.precision("qty"))
			min_production_qty = flt(self.get_min_qty(self.producible_qty), self.precision("qty"))

			if production_completion_qty and (production_completion_qty >= min_production_qty or self.status == "Stopped"):
				to_update.production_status = "Produced"
			elif self.status == "Stopped" or not flt(self.producible_qty, self.precision("qty")):
				to_update.production_status = "Not Applicable"
			else:
				to_update.production_status = "To Produce"
		else:
			to_update.production_status = "Not Applicable"

		# Update doc and database
		self.update(to_update)
		if update:
			self.db_set(to_update, update_modified=update_modified)

	def set_actual_dates(self, update=False, update_modified=True, ste_qty_map=None):
		if self.get("operations"):
			actual_start_dates = [getdate(d.actual_start_time) for d in self.get("operations") if d.actual_start_time]
			self.actual_start_date = min(actual_start_dates) if actual_start_dates else None

			actual_end_dates = [getdate(d.actual_end_time) for d in self.get("operations") if d.actual_end_time]
			if actual_end_dates and len(actual_end_dates) == len(self.operations):
				self.actual_end_date = max(actual_end_dates)
			else:
				self.actual_end_date = None
		else:
			if not ste_qty_map:
				ste_qty_map = self.get_ste_qty_map()

			if not self.skip_transfer:
				self.actual_start_date = ste_qty_map.get("Material Transfer for Manufacture", {}).get("min_posting_date")
			else:
				self.actual_start_date = ste_qty_map.get("Manufacture", {}).get("min_posting_date")

			if self.production_status == "Produced":
				self.actual_end_date = ste_qty_map.get("Manufacture", {}).get("max_posting_date")
			else:
				self.actual_end_date = None

			if not self.actual_start_date and self.subcontract_order_qty:
				send_to_subcontractor_date = frappe.db.sql("""
					select min(ste.posting_date)
					from `tabPurchase Order Item` po_item
					inner join `tabStock Entry` ste on ste.purchase_order = po_item.parent
					inner join `tabStock Entry Detail` ste_item on ste_item.parent = ste.name
					where ste.docstatus = 1 and po_item.docstatus = 1 and ste.purpose = 'Send to Subcontractor'
						and po_item.work_order = %s and ste_item.subcontracted_item = %s
				""", (self.name, self.production_item))

				if send_to_subcontractor_date:
					self.actual_start_date = send_to_subcontractor_date[0][0]

			if (not self.actual_end_date
				and self.subcontract_order_qty
				and self.production_status == "Produced"
				and self.subcontracting_status == "Received"
			):
				purchase_receipt_date = frappe.db.sql("""
					select max(pr.posting_date)
					from `tabPurchase Receipt Item` pr_item
					inner join `tabPurchase Receipt` pr on pr.name = pr_item.parent
					where pr.docstatus = 1 and pr_item.work_order = %s
				""", self.name)

				if purchase_receipt_date:
					self.actual_end_date = purchase_receipt_date[0][0]

		if update:
			self.db_set({
				"actual_start_date": self.actual_start_date,
				"actual_end_date": self.actual_end_date,
			}, update_modified=update_modified)

	def get_ste_qty_map(self):
		ste_qty_map = {}
		if self.docstatus == 1:
			ste_qty_data = frappe.db.sql("""
				select purpose,
					sum(fg_completed_qty) as fg_completed_qty,
					sum(process_loss_qty) as process_loss_qty,
					min(posting_date) as min_posting_date,
					max(posting_date) as max_posting_date
				from `tabStock Entry`
				where work_order = %s and docstatus = 1 and purpose in ('Manufacture', 'Material Transfer for Manufacture')
				group by purpose
			""", self.name, as_dict=1)

			for d in ste_qty_data:
				ste_qty_map[d.purpose] = d

		return ste_qty_map

	def set_completed_qty(self, update=False, update_modified=True):
		self.completed_qty = flt(self.produced_qty) + flt(self.subcontract_received_qty)
		self.per_completed = flt(self.completed_qty / self.qty * 100, 3)

		if update:
			self.db_set({
				"completed_qty": self.completed_qty,
				"per_completed": self.per_completed,
			}, update_modified=update_modified)

	def set_packing_status(self, update=False, update_modified=True):
		self.packed_qty = 0
		self.rejected_qty = 0
		self.reconciled_qty = 0
		self.last_packing_date = None

		if self.docstatus == 1:
			packing_data = frappe.db.sql("""
				select
					sum(psi.stock_qty - (psi.unpacked_return_qty * psi.conversion_factor)) as packed_qty,
					sum(psi.stock_rejected_qty) as rejected_qty,
					max(ps.posting_date) as max_posting_date
				from `tabPacking Slip Item` psi
				inner join `tabPacking Slip` ps on ps.name = psi.parent
				where ps.docstatus = 1
					and psi.work_order = %s
					and ifnull(psi.source_packing_slip, '') = ''
					and psi.qty != 0
			""", self.name, as_dict=1)

			self.packed_qty = flt(packing_data[0].packed_qty) if packing_data else 0
			self.rejected_qty = flt(packing_data[0].rejected_qty) if packing_data else 0
			self.last_packing_date = packing_data[0].max_posting_date if packing_data else None

			reject_loss_data = frappe.db.sql("""
				select ste.purpose, sum(i.stock_qty) as stock_qty
				from `tabStock Entry Detail` i
				inner join `tabStock Entry` ste on ste.name = i.parent
				where i.work_order = %s and ste.docstatus = 1 and ste.purpose in ('Material Issue', 'Material Transfer')
				group by ste.purpose
			""", self.name, as_dict=1)

			for d in reject_loss_data:
				if d.purpose == 'Material Issue':
					self.reconciled_qty += flt(d.stock_qty)
				elif d.purpose == 'Material Transfer':
					self.rejected_qty += flt(d.stock_qty)

		self.per_packed = flt(self.packed_qty / self.qty * 100, 3)

		if self.docstatus == 1:
			packed_qty = flt(self.packed_qty + self.rejected_qty + self.reconciled_qty, self.precision("qty"))
			min_packing_qty = flt(self.get_min_qty(self.completed_qty), self.precision("qty"))

			if self.packed_qty and (packed_qty >= min_packing_qty or self.status == "Stopped"):
				self.packing_status = "Packed"
			elif self.status == "Stopped" or not self.packing_slip_required or not self.completed_qty:
				self.packing_status = "Not Applicable"
			else:
				self.packing_status = "To Pack"
		else:
			self.packing_status = "Not Applicable"

		if update:
			self.db_set({
				"packed_qty": self.packed_qty,
				"rejected_qty": self.rejected_qty,
				"reconciled_qty": self.reconciled_qty,
				"packing_status": self.packing_status,
				"per_packed": self.per_packed,
				"last_packing_date": self.last_packing_date,
			}, update_modified=update_modified)

	def validate_overproduction(self, from_doctype=None):
		if from_doctype == "Purchase Order":
			subcontractable_qty = self.get_subcontractable_qty()
			if subcontractable_qty < 0:
				frappe.throw(_("{0} {1} {3} cannot be greater than pending quantity {2} {3} in {4}").format(
					self.meta.get_label("subcontract_order_qty"),
					frappe.bold(self.get_formatted("subcontract_order_qty")),
					frappe.bold(frappe.format(subcontractable_qty + self.subcontract_order_qty)),
					self.stock_uom,
					frappe.get_desk_link("Work Order", self.name)
				))

		max_production_qty = flt(self.get_qty_with_allowance(self.producible_qty), self.precision("qty"))

		for fieldname in ["produced_qty", "process_loss_qty", "material_transferred_for_manufacturing"]:
			qty = flt(self.get(fieldname), self.precision("qty"))
			if qty > max_production_qty:
				frappe.throw(_("{0} {1} {2} cannot be greater than maximum quantity {3} {2} in {4}").format(
					self.meta.get_label(fieldname),
					frappe.bold(self.get_formatted(fieldname)),
					self.stock_uom,
					frappe.bold(frappe.format(max_production_qty)),
					frappe.get_desk_link("Work Order", self.name)
				), StockOverProductionError)

		produced_qty = flt(self.produced_qty + self.process_loss_qty, self.precision("qty"))
		transferred_qty = flt(self.material_transferred_for_manufacturing, self.precision("qty"))
		if not self.skip_transfer and produced_qty > transferred_qty:
			frappe.throw(_("Production Completion Qty {0} {1} cannot be more than the Material Transferred for Manufacturing {2} {1} in {3}").format(
				frappe.bold(frappe.format(produced_qty, df=self.meta.get_field("produced_qty"))),
				self.stock_uom,
				frappe.bold(self.get_formatted("material_transferred_for_manufacturing")),
				frappe.get_desk_link("Work Order", self.name)
			), StockOverProductionError)

	def validate_overpacking(self, from_doctype=None):
		max_qty = flt(self.get_qty_with_allowance(self.qty), self.precision("qty"))
		for fieldname in ["packed_qty", "rejected_qty", "reconciled_qty"]:
			qty = flt(self.get(fieldname), self.precision("qty"))
			if qty > max_qty:
				frappe.throw(_("{0} {1} {2} cannot be greater than maximum quantity {3} {2} in {4}").format(
					self.meta.get_label(fieldname),
					frappe.bold(self.get_formatted(fieldname)),
					self.stock_uom,
					frappe.bold(frappe.format(max_qty)),
					frappe.get_desk_link("Work Order", self.name)
				))

		completed_qty = flt(self.completed_qty, self.precision("qty"))
		packed_qty = flt(self.packed_qty, self.precision("qty"))
		if packed_qty > completed_qty:
			frappe.throw(_("Packed Qty {0} {1} cannot be more than the Completed Qty {2} {1} in {3}").format(
				frappe.bold(self.get_formatted("packed_qty")),
				self.stock_uom,
				frappe.bold(self.get_formatted("completed_qty")),
				frappe.get_desk_link("Work Order", self.name)
			), StockOverProductionError)

		pack_reconcile_qty = flt(flt(self.packed_qty) + flt(self.rejected_qty) + flt(self.reconciled_qty),
			self.precision("qty"))

		if pack_reconcile_qty > completed_qty:
			frappe.throw(_("Packed + Rejected Qty {0} {1} cannot be more than the Completed Qty {2} {1} in {3}").format(
				frappe.bold(frappe.format(pack_reconcile_qty)),
				self.stock_uom,
				frappe.bold(self.get_formatted("completed_qty")),
				frappe.get_desk_link("Work Order", self.name)
			), StockOverProductionError)

	def get_qty_with_allowance(self, qty):
		return get_qty_with_allowance(qty, qty_to_produce=self.qty, max_qty=self.max_qty)

	def get_over_production_allowance(self):
		return get_over_production_allowance(qty_to_produce=self.qty, max_qty=self.max_qty)

	def get_min_qty(self, qty):
		under_production_allowance = flt(frappe.db.get_single_value("Manufacturing Settings", "under_production_allowance"))
		qty = flt(qty)
		return qty - (qty * under_production_allowance / 100)

	def get_producible_qty(self):
		return max(flt(self.qty) - flt(self.subcontract_order_qty), 0)

	def get_balance_qty(self, purpose):
		if purpose == "Material Transfer for Manufacture":
			return self.get_transfer_balance_qty()
		else:
			return self.get_production_balance_qty()

	def get_production_balance_qty(self):
		if self.skip_transfer:
			return max(flt(self.producible_qty) - flt(self.produced_qty), 0)
		else:
			material_remaining = flt(self.material_transferred_for_manufacturing) - flt(self.produced_qty) - flt(self.process_loss_qty)
			return max(material_remaining, 0)

	def get_transfer_balance_qty(self):
		return max(flt(self.producible_qty) - flt(self.material_transferred_for_manufacturing), 0)

	def get_subcontractable_qty(self):
		return get_subcontractable_qty(
			self.producible_qty,
			self.material_transferred_for_manufacturing,
			self.produced_qty,
		)

	def set_status(self, status=None, update=False, update_modified=True):
		previous_status = self.status

		if status:
			self.status = status

		if self.docstatus == 0:
			self.status = 'Draft'

		elif self.docstatus == 1:
			if self.status == "Stopped":
				self.status = "Stopped"
			elif self.completed_qty and self.production_status != "To Produce" and self.subcontracting_status != "To Receive":
				self.status = "Completed"
			elif self.subcontract_order_qty or self.has_stock_entry():
				self.status = "In Process"
			else:
				self.status = "Not Started"

		elif self.docstatus == 2:
			self.status = "Cancelled"

		self.add_status_comment(previous_status)

		if update:
			self.db_set('status', self.status, update_modified=update_modified)

	def has_stock_entry(self):
		return frappe.db.get_value("Stock Entry", {"work_order": self.name, "docstatus": 1})

	def update_sales_order_status(self):
		if self.get("sales_order"):
			doc = frappe.get_doc("Sales Order", self.sales_order)
			doc.set_production_packing_status(update=True)
			doc.notify_update()

	def update_production_plan_ordered_qty(self):
		if self.production_plan and self.production_plan_item:
			qty = self.qty if self.docstatus == 1 else 0
			frappe.db.set_value('Production Plan Item', self.production_plan_item, 'ordered_qty', qty)

			doc = frappe.get_doc('Production Plan', self.production_plan)
			doc.set_status()
			doc.db_set('status', doc.status)

	def update_production_plan_produced_qty(self):
		if not self.production_plan:
			return

		production_plan = frappe.get_doc('Production Plan', self.production_plan)
		completed_qty = 0
		if self.production_plan_item:
			total_qty = frappe.get_all("Work Order", fields="sum(completed_qty) as completed_qty", filters={
				'docstatus': 1,
				'production_plan': self.production_plan,
				'production_plan_item': self.production_plan_item
			}, as_list=1)

			completed_qty = total_qty[0][0] if total_qty else 0

		production_plan.run_method("update_produced_qty", completed_qty, self.production_plan_item)

	def update_material_request_ordered_qty(self):
		if self.material_request:
			doc = frappe.get_doc("Material Request", self.material_request)
			doc.set_completion_status(update=True)
			doc.validate_ordered_qty(from_doctype=self.doctype, row_names=[self.material_request_item])
			doc.set_status(update=True)

			doc.update_requested_qty([self.material_request_item])

			doc.notify_update()

	def update_planned_qty(self):
		update_bin_qty(self.production_item, self.fg_warehouse, {
			"planned_qty": get_planned_qty(self.production_item, self.fg_warehouse)
		})

	def update_reserved_qty_for_production(self):
		for d in self.required_items:
			if d.source_warehouse:
				stock_bin = get_bin(d.item_code, d.source_warehouse)
				stock_bin.update_reserved_qty_for_production()

	def delete_job_card(self):
		for d in frappe.get_all("Job Card", ["name"], {"work_order": self.name}):
			frappe.delete_doc("Job Card", d.name)

	def validate_cancel(self):
		if self.status == "Stopped":
			frappe.throw(_("Stopped Work Order cannot be cancelled, Unstop it first to cancel"))

		# Check whether any stock entry exists against this Work Order
		stock_entry = frappe.db.sql("""
			select name
			from `tabStock Entry`
			where work_order = %s and docstatus = 1
		""", self.name)

		if stock_entry:
			frappe.throw(_("Cannot cancel because submitted Stock Entry {0} exists").format(
				frappe.utils.get_link_to_form('Stock Entry', stock_entry[0][0]))
			)

	def update_status_on_cancel(self):
		self.db_set({
			"status": "Cancelled",
			"production_status": "Not Applicable",
			"subcontracting_status": "Not Applicable",
			"packing_status": "Not Applicable",
		})

	def get_holidays(self, workstation):
		holiday_list = frappe.db.get_value("Workstation", workstation, "holiday_list")

		holidays = {}

		if holiday_list not in holidays:
			holiday_list_days = [getdate(d[0]) for d in frappe.get_all("Holiday", fields=["holiday_date"],
				filters={"parent": holiday_list}, order_by="holiday_date", limit_page_length=0, as_list=1)]

			holidays[holiday_list] = holiday_list_days

		return holidays[holiday_list]

	def get_required_items_dict(self):
		item_dict = {}
		for d in self.required_items:
			item_dict[d.item_code] = d

		return item_dict

	@frappe.whitelist()
	def make_bom(self):
		data = frappe.db.sql(""" select sed.item_code, sed.qty, sed.s_warehouse
			from `tabStock Entry Detail` sed, `tabStock Entry` se
			where se.name = sed.parent and se.purpose = 'Manufacture'
			and (sed.t_warehouse is null or sed.t_warehouse = '') and se.docstatus = 1
			and se.work_order = %s""", (self.name), as_dict=1)

		bom = frappe.new_doc("BOM")
		bom.item = self.production_item
		bom.conversion_rate = 1

		for d in data:
			bom.append('items', {
				'item_code': d.item_code,
				'qty': d.qty,
				'source_warehouse': d.s_warehouse
			})

		if self.operations:
			bom.set('operations', self.operations)
			bom.with_operations = 1

		bom.set_bom_material_details()
		return bom

	@frappe.whitelist()
	def get_sub_assembly_items(self, for_raw_material_request=False, item_condition=None):
		"""Returns items with BOM that already do not have a linked work order"""
		work_order_items = []
		default_rm_warehouse = frappe.get_cached_value("Manufacturing Settings", None, "default_rm_warehouse")
		for_raw_material_request = cint(for_raw_material_request)

		for d in self.get("required_items"):
			if item_condition and not item_condition(d):
				continue

			bom_no = self.run_method("get_work_order_item_bom", d)
			if not bom_no:
				bom_no = get_default_bom(d.item_code)

			if not bom_no:
				continue

			stock_qty = flt(d.stock_required_qty)
			if for_raw_material_request:
				pending_qty = stock_qty
			else:
				work_order_data = frappe.db.sql("""
					select sum(qty)
					from `tabWork Order`
					where production_item = %s and parent_work_order = %s and work_order_item = %s and docstatus < 2
				""", (d.item_code, self.name, d.name))
				total_work_order_qty = flt(work_order_data[0][0]) if work_order_data else 0
				pending_qty = stock_qty - total_work_order_qty

			work_order_precison = frappe.get_precision("Work Order", "qty")
			pending_qty = round_up(pending_qty, work_order_precison)

			if pending_qty:
				wo_item = {
					"name": d.name,
					"item_code": d.item_code,
					"item_name": d.item_name,
					"description": d.description,
					"bom_no": bom_no,
					"warehouse": default_rm_warehouse if for_raw_material_request else d.source_warehouse,
					"stock_uom": d.get("stock_uom") or d.get("uom"),
					"parent_work_order": self.name,
					"work_order_item": d.name,
					"project": self.project,
					"cost_center": self.get("cost_center") or d.get("cost_center")
				}

				if for_raw_material_request:
					wo_item["required_qty"] = pending_qty
				else:
					wo_item["pending_qty"] = pending_qty
					wo_item["production_qty"] = pending_qty

				work_order_items.append(wo_item)

		return work_order_items


def get_qty_with_allowance(qty, qty_to_produce, max_qty):
	allowance_percentage = get_over_production_allowance(qty_to_produce, max_qty)
	return flt(qty) + flt(qty) * allowance_percentage / 100


def get_over_production_allowance(qty_to_produce, max_qty):
	if max_qty and qty_to_produce:
		allowance_percentage = flt(max_qty) / flt(qty_to_produce) * 100 - 100
	else:
		allowance_percentage = flt(frappe.get_cached_value("Manufacturing Settings", None, "overproduction_percentage_for_work_order"))

	return allowance_percentage


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_bom_operations(doctype, txt, searchfield, start, page_len, filters):
	if txt:
		filters['operation'] = ('like', '%%%s%%' % txt)

	return frappe.get_all('BOM Operation', filters=filters, fields=['operation'], as_list=1)


@frappe.whitelist()
def get_item_details(item, project=None):
	res = frappe.db.sql("""
		select item_name, stock_uom, description
		from `tabItem`
		where disabled=0
			and (end_of_life is null or end_of_life='0000-00-00' or end_of_life > %s)
			and name=%s
	""", (nowdate(), item), as_dict=1)

	if not res:
		return {}

	res = res[0]

	res["bom_no"] = get_default_bom(item, project=project)
	if not res["bom_no"]:
		frappe.throw(_("Active BOM for Item {0} not found").format(frappe.bold(item)))

	bom_data = frappe.db.get_value('BOM', res['bom_no'],
		['project', 'transfer_material_against', 'item_name'], as_dict=1)

	res['project'] = project or bom_data.pop("project")
	res.update(bom_data)

	res.update(check_if_scrap_warehouse_mandatory(res["bom_no"]))

	return res


@frappe.whitelist()
def make_work_order(bom_no, item, qty=0, project=None):
	if not frappe.has_permission("Work Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	item_details = get_item_details(item, project)

	wo_doc = frappe.new_doc("Work Order")
	wo_doc.production_item = item
	wo_doc.update(item_details)
	wo_doc.bom_no = bom_no

	if flt(qty) > 0:
		wo_doc.qty = flt(qty)
		wo_doc.get_items_and_operations_from_bom()

	return wo_doc


@frappe.whitelist()
def create_work_orders(
	items,
	company,
	ignore_version=True,
	ignore_feed=False,
	create_sub_assembly_work_orders=False
):
	return _create_work_orders(
		items,
		company,
		ignore_version=ignore_version,
		ignore_feed=ignore_feed,
		create_sub_assembly_work_orders=create_sub_assembly_work_orders,
	)


def _create_work_orders(
	items,
	company,
	ignore_permissions=False,
	ignore_version=True,
	ignore_feed=False,
	create_sub_assembly_work_orders=False
):
	'''Make Work Orders against the given Sales Order for the given `items`'''
	if isinstance(items, str):
		items = json.loads(items)

	work_order_docs = []

	for d in items:
		if not d.get("bom_no"):
			frappe.throw(_("Please select BOM No against Item {0}").format(d.get("item_code")))
		if not d.get("production_qty"):
			frappe.throw(_("Please select Qty against Item {0}").format(d.get("item_code")))

		sales_order = d.get("sales_order")
		parent_work_order = d.get("parent_work_order")
		customer = d.get("customer")
		customer_name = d.get("customer_name") if d.get("customer") else None
		delivery_date = d.get("delivery_date") or d.get("schedule_date") or d.get("expected_delivery_date")

		# set missing customer
		if not customer and sales_order:
			customer = frappe.db.get_value("Sales Order", sales_order, "customer", cache=1)
			customer_name = frappe.db.get_value("Sales Order", sales_order, "customer_name", cache=1)

		if not customer and parent_work_order:
			customer = frappe.db.get_value("Work Order", parent_work_order, "customer", cache=1)
			customer_name = frappe.db.get_value("Work Order", parent_work_order, "customer_name", cache=1)

		# set missing delivery date from sales order
		if not delivery_date and sales_order:
			delivery_date = frappe.db.get_value("Sales Order", sales_order, "delivery_date", cache=1)

		# set missing customer name
		if not customer_name and customer:
			customer_name = frappe.get_cached_value("Customer", customer, "customer_name")

		# set missing order line no
		order_line_no = cint(d.get("order_line_no"))
		if not order_line_no and d.get("sales_order_item"):
			order_line_no = frappe.db.get_value("Sales Order Item", d.get("sales_order_item"), 'idx')

		# create work order
		work_order = frappe.new_doc("Work Order")
		work_order.flags.ignore_permissions = cint(ignore_permissions)
		work_order.flags.ignore_version = cint(ignore_version)
		work_order.flags.ignore_feed = cint(ignore_feed)

		work_order.update({
			"production_item": d.get("item_code"),
			"item_name": d.get("item_name"),
			"description": d.get("description"),
			"bom_no": d.get("bom_no"),
			"qty": flt(d.get("production_qty")),
			"fg_warehouse": d.get("warehouse"),
			"company": company or d.get("company"),
			"sales_order": sales_order,
			"sales_order_item": d.get("sales_order_item"),
			"parent_work_order": parent_work_order,
			"work_order_item": d.get("work_order_item"),
			"customer": customer,
			"customer_name": customer_name,
			"project": d.get("project"),
			"order_line_no": order_line_no,
			"expected_delivery_date": delivery_date,
		})

		if parent_work_order:
			work_order.packing_slip_required = 0

		if work_order.meta.has_field("cost_center") and d.get("cost_center"):
			work_order.cost_center = d.get("cost_center")

		frappe.utils.call_hook_method("update_work_order_on_create", work_order, d)

		work_order.set_work_order_operations()
		work_order.save()

		if frappe.db.get_single_value("Manufacturing Settings", "auto_submit_work_order"):
			work_order.submit()

		work_order_docs.append(work_order)

	work_order_names = [p.name for p in work_order_docs]

	if cint(create_sub_assembly_work_orders):
		for work_order in work_order_docs:
			sub_assembly_items = work_order.get_sub_assembly_items()
			if sub_assembly_items:
				work_order_names += _create_work_orders(
					sub_assembly_items,
					company=company,
					ignore_permissions=ignore_permissions,
					ignore_version=ignore_version,
					ignore_feed=ignore_feed,
					create_sub_assembly_work_orders=True
				)

	return work_order_names


@frappe.whitelist()
def check_if_scrap_warehouse_mandatory(bom_no):
	res = {"set_scrap_wh_mandatory": False}
	if bom_no:
		bom = frappe.get_doc("BOM", bom_no)

		if len(bom.scrap_items) > 0:
			res["set_scrap_wh_mandatory"] = True

	return res


@frappe.whitelist()
def set_work_order_ops(name):
	po = frappe.get_doc('Work Order', name)
	po.set_work_order_operations()
	po.save()


@frappe.whitelist()
def finish_multiple_work_orders(work_orders, args=None):
	if work_orders and isinstance(work_orders, str):
		work_orders = json.loads(work_orders)

	if not work_orders:
		frappe.throw(_("Work Orders not selected"))

	_finish_multiple_work_orders.enqueue(work_orders=work_orders, args=args)
	frappe.msgprint(_("Processing Work Orders in background..."), alert=True)


@frappe.task(timeout=600)
def _finish_multiple_work_orders(work_orders, args=None):
	make_stock_entry_against_multiple_work_orders.catch(work_orders, args=args)


@frappe.catch_realtime_msgprint()
def make_stock_entry_against_multiple_work_orders(work_orders, args=None):
	if not work_orders:
		return

	frappe.publish_progress(
		0.5,
		title=_("Submitting Manufacture Entries..."),
		description=_("Submitting {0}/{1}").format(1, len(work_orders))
	)

	for i, d in enumerate(work_orders):
		work_order = d.get('work_order')
		qty = flt(d.get('finished_qty'))

		make_stock_entry(work_order, "Manufacture", qty, args=args, auto_submit=True)

		frappe.publish_progress(
			(i + 1) * 100 / len(work_orders),
			title=_("Submitting Manufacture Entries..."),
			description=_("Submitting {0}/{1}").format(min(i + 2, len(work_orders)), len(work_orders))
		)


@frappe.whitelist()
def make_stock_entry(
	work_order_id,
	purpose,
	qty=None,
	process_loss_qty=0,
	use_alternative_item=False,
	job_card=None,
	auto_submit=False,
	args=None
):
	if args and isinstance(args, str):
		args = json.loads(args)

	if not args:
		args = {}

	work_order = frappe.get_doc("Work Order", work_order_id)
	if not work_order.skip_transfer and not frappe.db.get_value("Warehouse", work_order.wip_warehouse, "is_group", cache=1):
		wip_warehouse = work_order.wip_warehouse
	else:
		wip_warehouse = None

	settings = frappe.get_cached_doc("Manufacturing Settings", None)

	stock_entry = frappe.new_doc("Stock Entry")

	for fieldname, value in args.items():
		if value and stock_entry.meta.has_field(fieldname):
			stock_entry.set(fieldname, value)

	stock_entry.pro_doc = work_order

	stock_entry.purpose = purpose
	stock_entry.work_order = work_order_id
	stock_entry.company = work_order.company
	stock_entry.from_bom = 1
	stock_entry.bom_no = work_order.bom_no
	stock_entry.job_card = job_card
	stock_entry.project = work_order.project
	stock_entry.use_multi_level_bom = work_order.use_multi_level_bom

	if qty is None:
		stock_entry.fg_completed_qty = work_order.get_balance_qty(purpose)
		stock_entry.fg_completed_qty = max(0.0, stock_entry.fg_completed_qty)
	else:
		stock_entry.fg_completed_qty = flt(qty)

	use_alternative_item = cint(use_alternative_item)
	stock_entry.use_alternative_item = use_alternative_item

	process_loss_qty = flt(process_loss_qty)
	stock_entry.process_loss_qty = max(0.0, process_loss_qty)

	if work_order.bom_no:
		stock_entry.inspection_required = frappe.db.get_value('BOM', work_order.bom_no, 'inspection_required')

	if work_order.meta.has_field("cost_center") and work_order.get("cost_center"):
		stock_entry.cost_center = work_order.cost_center

	if purpose == "Material Transfer for Manufacture":
		stock_entry.to_warehouse = wip_warehouse
		stock_entry.from_warehouse = work_order.source_warehouse
	else:
		stock_entry.from_warehouse = work_order.source_warehouse if work_order.skip_transfer else wip_warehouse

	if purpose == "Manufacture":
		stock_entry.to_warehouse = work_order.fg_warehouse

	frappe.utils.call_hook_method("update_stock_entry_from_work_order", stock_entry, work_order)

	stock_entry.set_stock_entry_type()
	stock_entry.get_items(auto_select_batches=work_order.auto_select_batches_in_stock_entry)

	def submit_stock_entry(ste):
		ste_copy = frappe.get_doc(copy.deepcopy(ste))
		# ste_copy.save()
		ste_copy.submit()
		frappe.msgprint(_("{0} submitted successfully ({1} {2}): {3}").format(
			purpose,
			stock_entry.get_formatted("fg_completed_qty"),
			work_order.stock_uom,
			frappe.get_desk_link("Stock Entry", ste_copy.name),
		), indicator="green")
		return ste_copy

	try:
		if purpose == "Material Transfer for Manufacture":
			if auto_submit or settings.auto_submit_material_transfer_entry:
				stock_entry = submit_stock_entry(stock_entry)
		elif purpose == "Manufacture":
			if auto_submit or settings.auto_submit_manufacture_entry:
				stock_entry = submit_stock_entry(stock_entry)
		else:
			if auto_submit:
				stock_entry = submit_stock_entry(stock_entry)
	except StockOverProductionError:
		raise
	except frappe.ValidationError:
		if auto_submit:
			raise
		else:
			frappe.db.rollback()

	return stock_entry.as_dict()


@frappe.whitelist()
def get_default_warehouse():
	wip_warehouse = frappe.get_cached_value("Manufacturing Settings", None, "default_wip_warehouse")
	fg_warehouse = frappe.get_cached_value("Manufacturing Settings", None, "default_fg_warehouse")
	rm_warehouse = frappe.get_cached_value("Manufacturing Settings", None, "default_rm_warehouse")
	return {"wip_warehouse": wip_warehouse, "fg_warehouse": fg_warehouse, "source_warehouse": rm_warehouse}


@frappe.whitelist()
def stop_unstop(work_order, status):
	""" Called from client side on Stop/Unstop event"""

	if not frappe.has_permission("Work Order", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	pro_order = frappe.get_doc("Work Order", work_order)
	pro_order.run_method("update_status", status)
	pro_order.notify_update()

	frappe.msgprint(_("{0} has been {1}").format(frappe.get_desk_link("Work Order", work_order), status))

	return pro_order.status


@frappe.whitelist()
def create_job_cards(work_order, operations):
	if isinstance(operations, str):
		operations = json.loads(operations)

	work_order = frappe.get_doc('Work Order', work_order)
	for row in operations:
		validate_operation_data(row)
		doc = _make_job_card(work_order, row, row.get("qty"))
		doc.flags.ignore_mandatory = True
		doc.insert()
		frappe.msgprint(_("Job card {0} created").format(get_link_to_form("Job Card", doc.name)))


def validate_operation_data(row):
	if row.get("qty") <= 0:
		frappe.throw(_("Quantity to Produce can not be zero for the operation {0}").format(
			frappe.bold(row.get("operation"))
		))

	if row.get("qty") > row.get("pending_qty"):
		frappe.throw(_("For operation {0}: Quantity ({1}) can not be greter than pending quantity ({2})").format(
			frappe.bold(row.get("operation")), frappe.bold(row.get("qty")), frappe.bold(row.get("pending_qty"))
		))


@frappe.whitelist()
def make_job_card(work_order, operation, workstation, qty, auto_submit=False):
	pro_doc = frappe.get_doc("Work Order", work_order)
	qty = flt(qty)

	operation_row = [d for d in pro_doc.operations if d.operation == operation]
	if operation_row:
		operation_row = operation_row[0]
	else:
		frappe.throw(_("Operation {0} not in Work Order {1}").format(operation, work_order))

	job_card_doc = _make_job_card(pro_doc, operation_row, qty)
	job_card_doc.workstation = workstation

	if auto_submit or frappe.db.get_single_value('Manufacturing Settings', 'auto_submit_job_card'):
		job_card_doc.submit()

		frappe.msgprint(_("{0} submitted successfully for Operation {1} ({2} {3})").format(
			frappe.get_desk_link("Job Card", job_card_doc.name),
			frappe.bold(job_card_doc.operation),
			job_card_doc.get_formatted("for_quantity"),
			pro_doc.stock_uom,
		), indicator="green")

	return job_card_doc


def _make_job_card(work_order, row, qty):
	doc = frappe.new_doc("Job Card")
	doc.update({
		'work_order': work_order.name,
		'operation': row.get("operation"),
		'workstation': row.get("workstation"),
		'posting_date': getdate(),
		'for_quantity': flt(qty),
		'operation_id': row.get("name"),
		'bom_no': work_order.bom_no,
		'project': work_order.project,
		'company': work_order.company,
		'wip_warehouse': work_order.wip_warehouse
	})

	frappe.utils.call_hook_method("update_job_card_on_create", doc)
	doc.run_method("set_missing_values")

	return doc


def get_subcontractable_qty(producible_qty, material_transferred_for_manufacturing, produced_qty):
	production_completed_qty = max(flt(produced_qty), flt(material_transferred_for_manufacturing))
	subcontractable_qty = flt(producible_qty) - production_completed_qty
	return subcontractable_qty


@frappe.whitelist()
def create_pick_list(source_name, target_doc=None, for_qty=None):
	for_qty = for_qty or json.loads(target_doc).get('for_qty')
	max_finished_goods_qty = frappe.db.get_value('Work Order', source_name, 'qty')

	def update_item_quantity(source, target, source_parent, target_parent):
		pending_to_issue = flt(source.required_qty) - flt(source.transferred_qty)
		desire_to_transfer = flt(source.required_qty) / max_finished_goods_qty * flt(for_qty)

		qty = 0
		if desire_to_transfer <= pending_to_issue:
			qty = desire_to_transfer
		elif pending_to_issue > 0:
			qty = pending_to_issue

		if qty:
			target.qty = qty
			target.stock_qty = qty
			target.uom = frappe.get_value('Item', source.item_code, 'stock_uom')
			target.stock_uom = target.uom
			target.conversion_factor = 1
		else:
			target.delete()

	doc = get_mapped_doc('Work Order', source_name, {
		'Work Order': {
			'doctype': 'Pick List',
			'validation': {
				'docstatus': ['=', 1]
			}
		},
		'Work Order Item': {
			'doctype': 'Pick List Item',
			'postprocess': update_item_quantity,
			'condition': lambda doc, source, target: abs(doc.transferred_qty) < abs(doc.required_qty)
		},
	}, target_doc)

	doc.for_qty = for_qty

	doc.set_item_locations()

	return doc


@frappe.whitelist()
def make_packing_slip(work_orders, target_doc=None):
	from erpnext.selling.doctype.sales_order.sales_order import make_packing_slip as make_packing_slip_from_so

	if isinstance(work_orders, str):
		work_orders = json.loads(work_orders)

	if not work_orders:
		frappe.throw(_("Please select Work Order(s) to pack"))

	pack_from_sales_orders = {}
	pack_from_work_orders = []

	customers = set()

	# Validate and separate sales order work orders
	for name in work_orders:
		wo_doc = frappe.get_doc("Work Order", name)

		if not wo_doc or wo_doc.docstatus != 1:
			continue
		if wo_doc.packed_qty >= wo_doc.completed_qty:
			continue

		if wo_doc.customer:
			customers.add(wo_doc.customer)
			if len(customers) > 1:
				frappe.throw(_("Cannot pack Work Orders for multiple customers"))

		if wo_doc.sales_order and wo_doc.sales_order_item:
			pack_from_sales_orders.setdefault(wo_doc.sales_order, []).append(wo_doc.sales_order_item)
		else:
			pack_from_work_orders.append(wo_doc)

	# Empty packable work order list error
	if not pack_from_sales_orders and not pack_from_work_orders:
		frappe.throw(_("Selected Work Order(s) are not applicable for packing"))

	# Map from Sales Orders first
	for sales_order, sales_order_items in pack_from_sales_orders.items():
		frappe.flags.selected_children = {"items": sales_order_items}
		target_doc = make_packing_slip_from_so(sales_order, target_doc)
		frappe.flags.selected_children = None

	# Map from Work Orders
	if not target_doc:
		target_doc = frappe.new_doc("Packing Slip")

	for wo_doc in pack_from_work_orders:
		if wo_doc.customer and not target_doc.customer:
			target_doc.customer = wo_doc.customer
		if wo_doc.fg_warehouse and not target_doc.target_warehouse:
			target_doc.target_warehouse = wo_doc.fg_warehouse
		if target_doc.meta.has_field("cost_center") and wo_doc.get("cost_center") and not target_doc.get("cost_center"):
			target_doc.cost_center = wo_doc.get("cost_center")

		row = target_doc.append("items", frappe.new_doc("Packing Slip Item"))
		row.work_order = wo_doc.name
		row.item_code = wo_doc.production_item
		row.item_name = wo_doc.item_name
		row.source_warehouse = wo_doc.wip_warehouse if wo_doc.produce_fg_in_wip_warehouse else wo_doc.fg_warehouse
		row.qty = wo_doc.completed_qty - wo_doc.packed_qty - wo_doc.rejected_qty - wo_doc.reconciled_qty
		row.uom = wo_doc.stock_uom

		frappe.utils.call_hook_method("postprocess_work_order_to_packing_slip_item", wo_doc, target_doc, row)

	# Post process if necessary
	if pack_from_work_orders:
		frappe.utils.call_hook_method("postprocess_work_orders_to_packing_slip", pack_from_work_orders, target_doc)
		target_doc.run_method("set_missing_values")
		target_doc.run_method("calculate_totals")

	return target_doc


@frappe.whitelist()
def make_purchase_order(work_orders, target_doc=None, supplier=None):
	if isinstance(work_orders, str):
		work_orders = json.loads(work_orders)

	if not work_orders:
		frappe.throw(_("Please select Work Order(s) to subcontract"))

	if not target_doc:
		target_doc = frappe.new_doc("Purchase Order")
	if target_doc and isinstance(target_doc, str):
		target_doc = frappe.get_doc(json.loads(target_doc))

	for name in work_orders:
		wo = frappe.get_doc("Work Order", name)
		if wo.docstatus != 1:
			continue
		if wo.get_subcontractable_qty() <= 0:
			continue

		if supplier:
			target_doc.supplier = supplier

		target_doc.is_subcontracted = 1

		row = target_doc.append("items", frappe.new_doc("Purchase Order Item"))
		row.item_code = wo.production_item
		row.warehouse = wo.fg_warehouse

		row.qty = wo.get_subcontractable_qty()
		row.uom = wo.stock_uom

		row.work_order = wo.name
		row.schedule_date = wo.expected_delivery_date

		row.bom = wo.bom_no
		row.include_exploded_items = wo.use_multi_level_bom

		if wo.sales_order and wo.sales_order_item:
			row.sales_order = wo.sales_order
			row.sales_order_item = wo.sales_order_item

		if wo.meta.has_field("cost_center") and wo.get("cost_center") and not target_doc.get("cost_center"):
			target_doc.cost_center = wo.get("cost_center")

		frappe.utils.call_hook_method("update_purchase_order_from_work_order", target_doc, row, wo)

	target_doc.run_method("set_missing_values")
	target_doc.run_method("calculate_taxes_and_totals")

	return target_doc
