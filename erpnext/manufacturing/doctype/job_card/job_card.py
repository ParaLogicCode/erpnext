# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt, time_diff_in_hours, get_datetime, get_link_to_form
from frappe.model.mapper import get_mapped_doc
from frappe.model.document import Document


class OperationMismatchError(frappe.ValidationError): pass


class JobCard(Document):
	def validate(self):
		self.set_missing_values()
		self.validate_qty()
		self.validate_time_logs()
		self.validate_operation_id()
		self.set_status()

	def before_submit(self):
		self.set_actual_dates()

	def on_submit(self):
		self.validate_job_card()
		self.update_work_order()
		self.set_transferred_qty()
		self.create_material_consumption_entry()

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		self.update_work_order()
		self.set_transferred_qty()
		self.cancel_material_consumption_entry()

	def set_missing_values(self):
		work_order_details = frappe._dict()
		if self.work_order:
			work_order_details = get_work_order_details(self.work_order)
			self.update(work_order_details)

		operation_material_consumption_required = frappe.get_cached_value("Operation", self.operation,
			"material_consumption_required")
		self.material_consumption_required = cint(bool(work_order_details.allow_material_consumption and operation_material_consumption_required))

		if not self.get("items"):
			self.set_required_items()

	def validate_qty(self):
		if flt(self.for_quantity) <= 0:
			frappe.throw(_("Qty to Produce must be greater than 0"))

	def validate_time_logs(self):
		if cint(frappe.get_cached_value("Manufacturing Settings", None, "disable_capacity_planning")):
			self.time_logs = []
			self.total_completed_qty = flt(self.for_quantity) if self.docstatus == 1 else 0
			return

		self.total_completed_qty = 0.0
		self.total_time_in_mins = 0.0

		if self.get('time_logs'):
			for d in self.get('time_logs'):
				if get_datetime(d.from_time) > get_datetime(d.to_time):
					frappe.throw(_("Row {0}: From time must be less than to time").format(d.idx))

				data = self.get_overlap_for(d)
				if data:
					frappe.throw(_("Row {0}: From Time and To Time of {1} is overlapping with {2}")
						.format(d.idx, self.name, data.name))

				if d.from_time and d.to_time:
					d.time_in_mins = time_diff_in_hours(d.to_time, d.from_time) * 60
					self.total_time_in_mins += d.time_in_mins

				if d.completed_qty:
					self.total_completed_qty += d.completed_qty

	def get_overlap_for(self, args):
		existing = frappe.db.sql("""select jc.name as name from
			`tabJob Card Time Log` jctl, `tabJob Card` jc where jctl.parent = jc.name and
			(
				(%(from_time)s > jctl.from_time and %(from_time)s < jctl.to_time) or
				(%(to_time)s > jctl.from_time and %(to_time)s < jctl.to_time) or
				(%(from_time)s <= jctl.from_time and %(to_time)s >= jctl.to_time))
			and jctl.name!=%(name)s
			and jc.name!=%(parent)s
			and jc.docstatus < 2
			and jc.employee = %(employee)s """,
			{
				"from_time": args.from_time,
				"to_time": args.to_time,
				"name": args.name or "No Name",
				"parent": args.parent or "No Name",
				"employee": self.employee
			}, as_dict=True)

		return existing[0] if existing else None

	def validate_operation_id(self):
		if (self.get("operation_id") and self.get("operation_row_number") and self.operation and self.work_order and
			frappe.get_cached_value("Work Order Operation", self.operation_row_number, "name") != self.operation_id):
			work_order = frappe.bold(get_link_to_form("Work Order", self.work_order))
			frappe.throw(_("Operation {0} does not belong to the work order {1}")
				.format(frappe.bold(self.operation), work_order), OperationMismatchError)

	def set_status(self, update_status=False):
		if self.status == "On Hold":
			return

		self.status = {
			0: "Open",
			1: "Submitted",
			2: "Cancelled"
		}[self.docstatus or 0]

		if self.time_logs:
			self.status = 'Work In Progress'

		if self.docstatus == 1 and (self.for_quantity == self.transferred_qty or not self.material_transfer_required):
			self.status = 'Completed'

		if self.status != 'Completed':
			if self.for_quantity == self.transferred_qty:
				self.status = 'Material Transferred'

		if update_status:
			self.db_set('status', self.status)

	@frappe.whitelist()
	def set_required_items(self):
		self.items = []

		if not self.get('work_order'):
			return

		doc = frappe.get_doc('Work Order', self.get('work_order'))
		material_transfer_required = doc.transfer_material_against == 'Job Card' and not doc.skip_transfer
		if not material_transfer_required and not self.material_consumption_required:
			return

		for d in doc.required_items:
			if material_transfer_required and not d.operation and not d.skip_transfer_for_manufacture:
				frappe.throw(_("Row {0}: Operation is not set against Raw Material {1} in {2}").format(
					d.idx,
					frappe.get_desk_link("Item", d.item_code),
					frappe.get_desk_link("Work Order", self.work_order)
				))

			if self.get('operation') == d.operation:
				self.append('items', {
					'item_code': d.item_code,
					'item_name': d.item_name,
					'description': d.description,
					'source_warehouse': d.source_warehouse,
					'required_qty': (d.required_qty * flt(self.for_quantity)) / doc.qty,
					'uom': d.uom,
				})

	def validate_job_card(self):
		if not self.time_logs and not cint(frappe.get_cached_value("Manufacturing Settings", None, "disable_capacity_planning")):
			frappe.throw(_("Time logs are required for {0} {1}")
				.format(frappe.bold("Job Card"), get_link_to_form("Job Card", self.name)))

		if self.for_quantity and self.total_completed_qty != self.for_quantity:
			total_completed_qty = frappe.bold(_("Total Completed Qty"))
			qty_to_manufacture = frappe.bold(_("Qty to Produce"))

			frappe.throw(_("The {0} ({1}) must be equal to {2} ({3})"
				.format(total_completed_qty, frappe.bold(self.total_completed_qty), qty_to_manufacture,frappe.bold(self.for_quantity))))

	def update_work_order(self):
		if self.work_order:
			doc = frappe.get_doc("Work Order", self.work_order)
			doc.set_operation_status(update=True)
			doc.validate_completed_qty_in_operations(from_doctype=self.doctype)
			doc.set_actual_dates(update=True)
			doc.notify_update()

	def set_transferred_qty(self, update_status=False):
		if not self.items:
			self.transferred_qty = self.for_quantity if self.docstatus == 1 else 0

		if not self.material_transfer_required:
			return

		if self.items:
			self.transferred_qty = frappe.db.get_value('Stock Entry', {
				'job_card': self.name,
				'work_order': self.work_order,
				'docstatus': 1
			}, 'sum(fg_completed_qty)') or 0

		self.db_set("transferred_qty", self.transferred_qty)

		qty = 0
		if self.work_order:
			doc = frappe.get_doc('Work Order', self.work_order)
			if doc.transfer_material_against == 'Job Card' and not doc.skip_transfer:
				completed = True
				for d in doc.operations:
					if d.status != 'Completed':
						completed = False
						break

				if completed:
					job_cards = frappe.get_all('Job Card', filters = {'work_order': self.work_order,
						'docstatus': ('!=', 2)}, fields = 'sum(transferred_qty) as qty', group_by='operation_id')

					if job_cards:
						qty = min([d.qty for d in job_cards])

			doc.db_set('material_transferred_for_manufacturing', qty)

		self.set_status(update_status)

	def create_material_consumption_entry(self):
		from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
		if not self.material_consumption_required or not self.items:
			return

		make_stock_entry(
			self.work_order,
			purpose="Material Consumption for Manufacture",
			qty=self.for_quantity,
			job_card=self.name,
			auto_submit=True
		)

	def cancel_material_consumption_entry(self):
		if not self.material_consumption_required or not self.items:
			return

		stock_entry = frappe.db.get_value("Stock Entry", {
			"job_card": self.name, "purpose": "Material Consumption for Manufacture", "docstatus": 1,
		})

		if stock_entry:
			frappe.get_doc("Stock Entry", stock_entry).cancel()

	def set_actual_dates(self):
		if self.time_logs:
			self.actual_start_dt = min(get_datetime(d.from_time) for d in self.time_logs)
			self.actual_end_dt = max(get_datetime(d.to_time) for d in self.time_logs)
		else:
			self.actual_start_dt = self.actual_end_dt = get_datetime()

@frappe.whitelist()
def get_operation_details(work_order, operation):
	if work_order and operation:
		return frappe.get_all("Work Order Operation",
			fields=["name", "idx"],
			filters={
				"parent": work_order,
				"operation": operation
			}
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_operations(doctype, txt, searchfield, start, page_len, filters):
	if filters and filters.get("work_order"):
		args = {"parent": filters.get("work_order")}
		if txt:
			args["operation"] = ("like", "%{0}%".format(txt))

		return frappe.get_all("Work Order Operation",
			filters=args,
			fields=["distinct operation as operation"],
			limit_start=start,
			limit_page_length=page_len,
			order_by="idx asc",
			as_list=1
		)
	else:
		return []


@frappe.whitelist()
def make_material_request(source_name, target_doc=None):
	def update_item(obj, target, source_parent, target_parent):
		target.warehouse = source_parent.wip_warehouse

	def set_missing_values(source, target):
		target.material_request_type = "Material Transfer"

	doclist = get_mapped_doc("Job Card", source_name, {
		"Job Card": {
			"doctype": "Material Request",
			"field_map": {
				"name": "job_card",
			},
		},
		"Job Card Item": {
			"doctype": "Material Request Item",
			"field_map": {
				"required_qty": "qty",
				"uom": "stock_uom"
			},
			"postprocess": update_item,
		}
	}, target_doc, set_missing_values)

	return doclist


@frappe.whitelist()
def make_material_transfer(source_name, target_doc=None):
	def update_item(obj, target, source_parent, target_parent):
		target.t_warehouse = source_parent.wip_warehouse

	def set_missing_values(source, target):
		target.purpose = "Material Transfer for Manufacture"
		target.from_bom = 1
		target.fg_completed_qty = source.get('for_quantity', 0) - source.get('transferred_qty', 0)
		target.set_stock_entry_type()
		target.run_method("postprocess_after_mapping")

	doclist = get_mapped_doc("Job Card", source_name, {
		"Job Card": {
			"doctype": "Stock Entry",
			"field_map": {
				"name": "job_card",
				"for_quantity": "fg_completed_qty"
			},
		},
		"Job Card Item": {
			"doctype": "Stock Entry Detail",
			"field_map": {
				"source_warehouse": "s_warehouse",
				"required_qty": "qty",
				"uom": "uom"
			},
			"postprocess": update_item,
		}
	}, target_doc, set_missing_values)

	return doclist


@frappe.whitelist()
def get_work_order_details(work_order):
	details = frappe.db.get_value("Work Order", work_order, [
		"production_item", "item_name", "bom_no", "stock_uom",
		"skip_transfer", "transfer_material_against", "allow_material_consumption",
		"wip_warehouse", "project", "company",
	], as_dict=1) if work_order else {}

	if not details:
		frappe.throw(_("Work Order {0} does not exist").format(work_order))

	out = frappe._dict({
		"production_item": details.production_item,
		"item_name": details.item_name,
		"bom_no": details.bom_no,
		"stock_uom": details.stock_uom,
		"wip_warehouse": details.wip_warehouse,
		"project": details.project,
		"allow_material_consumption": details.allow_material_consumption,
		"material_transfer_required": cint(details.transfer_material_against == "Job Card" and not details.skip_transfer),
	})

	return out


@frappe.whitelist()
def get_job_details(start, end, filters=None):
	events = []

	event_color = {
		"Completed": "#cdf5a6",
		"Material Transferred": "#ffdd9e",
		"Work In Progress": "#D3D3D3"
	}

	from frappe.desk.reportview import get_filters_cond
	conditions = get_filters_cond("Job Card", filters, [])

	job_cards = frappe.db.sql(""" SELECT `tabJob Card`.name, `tabJob Card`.work_order,
			`tabJob Card`.employee_name, `tabJob Card`.status, ifnull(`tabJob Card`.remarks, ''),
			min(`tabJob Card Time Log`.from_time) as from_time,
			max(`tabJob Card Time Log`.to_time) as to_time
		FROM `tabJob Card` , `tabJob Card Time Log`
		WHERE
			`tabJob Card`.name = `tabJob Card Time Log`.parent {0}
			group by `tabJob Card`.name""".format(conditions), as_dict=1)

	for d in job_cards:
			subject_data = []
			for field in ["name", "work_order", "remarks", "employee_name"]:
				if not d.get(field): continue

				subject_data.append(d.get(field))

			color = event_color.get(d.status)
			job_card_data = {
				'from_time': d.from_time,
				'to_time': d.to_time,
				'name': d.name,
				'subject': '\n'.join(subject_data),
				'color': color if color else "#89bcde"
			}

			events.append(job_card_data)

	return events
