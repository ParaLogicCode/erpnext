# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
import erpnext
from frappe.utils import cint, cstr, flt
from frappe import _
from erpnext.setup.utils import get_exchange_rate
from frappe.model.document import Document
from erpnext.stock.get_item_details import (get_conversion_factor, get_price_list_data, get_default_warehouse,
	get_default_cost_center)
from erpnext.stock.doctype.item_alternative.item_alternative import has_alternative_item
from frappe.core.doctype.version.version import get_diff
from frappe.model.utils import get_fetch_values
import functools
from operator import itemgetter


form_grid_templates = {
	"items": "templates/form_grid/item_grid.html"
}

force_fields = ["stock_uom"]


class BOM(Document):
	def get_feed(self):
		return "For {0}".format(self.get('item_name') or self.get('item_code') or self.get('name'))

	def autoname(self):
		names = frappe.db.sql_list("""select name from `tabBOM` where item=%s""", self.item)

		if names:
			# name can be BOM/ITEM/001, BOM/ITEM/001-1, BOM-ITEM-001, BOM-ITEM-001-1

			# split by item
			names = [name.split(self.item, 1) for name in names]
			names = [d[-1][1:] for d in filter(lambda x: len(x) > 1 and x[-1], names)]

			# split by (-) if cancelled
			if names:
				names = [cint(name.split('-')[-1]) for name in names]
				idx = max(names) + 1
			else:
				idx = 1
		else:
			idx = 1

		self.name = 'BOM-' + self.item + ('-%.3i' % idx)

	def validate(self):
		self.validate_main_item()
		self.validate_currency()
		self.set_conversion_rate()
		self.set_plc_conversion_rate()
		self.validate_uom_is_interger()
		self.set_bom_material_details()
		self.validate_materials()
		self.validate_operations()
		self.update_cost(update_parent=False, from_child_bom=True, save=False)
		self.calculate_cost()

	def on_update(self):
		frappe.cache.hdel('bom_children', self.name)
		self.check_recursion()
		self.update_stock_qty()
		self.update_exploded_items()

	def before_submit(self):
		self.set_item_operation()

	def on_submit(self):
		self.manage_default_bom()

	def on_cancel(self):
		self.db_set("is_active", 0)
		self.db_set("is_default", 0)
		self.validate_bom_links()
		self.manage_default_bom()

	def on_update_after_submit(self):
		self.validate_bom_links()
		self.manage_default_bom()

	def onload(self):
		if self.docstatus == 0:
			if self.get('item'):
				self.update(get_fetch_values(self.doctype, 'item', self.item))

	def before_print(self, print_settings=None):
		self.company_address_doc = erpnext.get_company_address_doc(self)

		if self.docstatus == 0:
			if self.get('item'):
				self.update(get_fetch_values(self.doctype, 'item', self.item))

	@frappe.whitelist()
	def get_routing(self):
		if self.routing:
			self.set("operations", [])
			for d in frappe.get_all("BOM Operation", fields = ["*"],
				filters = {'parenttype': 'Routing', 'parent': self.routing}, order_by="idx"):
				child = self.append('operations', {
					"operation": d.operation,
					"workstation": d.workstation,
					"description": d.description,
					"time_in_mins": d.time_in_mins,
					"batch_size": d.batch_size,
					"operating_cost": d.operating_cost,
					"idx": d.idx
				})
				child.hour_rate = flt(d.hour_rate / self.conversion_rate, 2)

	def validate_rm_item(self, item):
		if (item.name in [it.item_code for it in self.items]) and item.name == self.item:
			frappe.throw(_("BOM #{0}: Raw material cannot be same as main Item").format(self.name))

	def set_bom_material_details(self):
		for item in self.get("items"):
			self.validate_bom_currecny(item)

			if item.do_not_explode:
				item.bom_no = None

			material_details = self.get_bom_material_detail({
				"item_code": item.item_code,
				"item_name": item.item_name,
				"bom_no": item.bom_no,
				"stock_qty": item.stock_qty,
				"qty": item.qty,
				"uom": item.uom,
				"stock_uom": item.stock_uom,
				"conversion_factor": item.conversion_factor,
				"skip_transfer_for_manufacture": item.skip_transfer_for_manufacture,
				"do_not_explode": item.do_not_explode,
			})

			for key in material_details:
				if not item.get(key) or key in force_fields:
					item.set(key, material_details[key])

	@frappe.whitelist()
	def get_bom_material_detail(self, args=None):
		""" Get raw material details like uom, desc and rate"""
		if not args:
			args = frappe.form_dict.get('args')

		if isinstance(args, str):
			import json
			args = json.loads(args)

		item = frappe.get_cached_doc("Item", args.get('item_code'))
		self.validate_rm_item(item)

		args['bom_no'] = args.get('bom_no') or item.default_bom or ''

		if args.get('skip_transfer_for_manufacture') is not None:
			args['skip_transfer_for_manufacture'] = cint(args.get('skip_transfer_for_manufacture'))
		else:
			args['skip_transfer_for_manufacture'] = cint(item.skip_transfer_for_manufacture)

		if not args.get('uom') and item.get('manufacture_uom'):
			args['uom'] = item.get('manufacture_uom')
			args['conversion_factor'] = get_conversion_factor(item.name, args['uom']).get("conversion_factor") or 1
			args['qty'] = flt(args.get('qty')) or 1
			args['stock_qty'] = args['qty'] * args['conversion_factor']

		rate = self.get_rm_rate(args)
		ret_item = {
			'item_name': item.item_name,
			'description': item.description,
			'image': item.image,
			'stock_uom': item.stock_uom,
			'uom': args.get('uom') or item.get('stock_uom'),
			'conversion_factor': args.get('conversion_factor') or 1,
			'bom_no': args.get('bom_no') if not args.get('do_not_explode') else None,
			'rate': rate,
			'qty': flt(args.get("qty")) or flt(args.get("stock_qty")) or 1,
			'stock_qty': flt(args.get("stock_qty")) or flt(args.get("qty")) or 1,
			'base_rate': flt(rate) * (flt(self.conversion_rate) or 1),
			'skip_transfer_for_manufacture': args.get('skip_transfer_for_manufacture'),
			'has_alternative_item': has_alternative_item(item.name)
		}

		return ret_item

	def validate_bom_currecny(self, item):
		if item.get('bom_no') and frappe.db.get_value('BOM', item.get('bom_no'), 'currency') != self.currency:
			frappe.throw(_("Row {0}: Currency of the BOM #{1} should be equal to the selected currency {2}")
				.format(item.idx, item.bom_no, self.currency))

	def get_rm_rate(self, arg):
		"""	Get raw material rate as per selected method, if bom exists takes bom cost """
		rate = 0
		if not self.rm_cost_as_per:
			self.rm_cost_as_per = "Valuation Rate"

		if arg.get('scrap_items'):
			rate = self.get_valuation_rate(arg)
		elif arg:
			#Customer Provided parts will have zero rate
			if not frappe.get_cached_value('Item', arg["item_code"], 'is_customer_provided_item'):
				if arg.get('bom_no') and self.set_rate_of_sub_assembly_item_based_on_bom:
					rate = flt(self.get_bom_unitcost(arg['bom_no'])) * (arg.get("conversion_factor") or 1)
				else:
					if self.rm_cost_as_per == 'Valuation Rate':
						rate = self.get_valuation_rate(arg) * (arg.get("conversion_factor") or 1)
					elif self.rm_cost_as_per == 'Last Purchase Rate':
						rate = flt(arg.get('last_purchase_rate') \
							or frappe.db.get_value("Item", arg['item_code'], "last_purchase_rate")) \
								* (arg.get("conversion_factor") or 1)
					elif self.rm_cost_as_per == "Price List":
						if not self.buying_price_list:
							frappe.throw(_("Please select Price List"))
						args = frappe._dict({
							"doctype": "BOM",
							"price_list": self.buying_price_list,
							"qty": flt(arg.get("qty")) or 1,
							"uom": arg.get("uom") or arg.get("stock_uom"),
							"stock_uom": arg.get("stock_uom"),
							"transaction_type": "buying",
							"company": self.company,
							"currency": self.currency,
							"conversion_rate": 1, # Passed conversion rate as 1 purposefully, as conversion rate is applied at the end of the function
							"conversion_factor": arg.get("conversion_factor") or 1,
							"plc_conversion_rate": 1,
							"ignore_party": True
						})
						item_doc = frappe.get_cached_doc("Item", arg.get("item_code"))
						out = frappe._dict()
						get_price_list_data(args, item_doc, out)
						rate = out.price_list_rate

					if not rate:
						if self.rm_cost_as_per == "Price List":
							frappe.msgprint(_("Price not found for item {0} in price list {1}")
								.format(arg["item_code"], self.buying_price_list), alert=True)
						else:
							frappe.msgprint(_("{0} not found for item {1}")
								.format(self.rm_cost_as_per, arg["item_code"]), alert=True)

		return flt(rate) * flt(self.plc_conversion_rate or 1) / (self.conversion_rate or 1)

	@frappe.whitelist()
	def update_cost(self, update_parent=True, from_child_bom=False, save=True):
		if self.docstatus == 2:
			return
		if self.docstatus != 1:
			save = False

		existing_bom_cost = self.total_cost

		for d in self.get("items"):
			if not d.get("item_code"):
				continue

			d.conversion_factor = get_conversion_factor(d.item_code, d.uom).get("conversion_factor") or 1
			d.stock_qty = flt(d.conversion_factor) * flt(d.qty)
			d.update(get_fetch_values(d.doctype, 'item_code', d.item_code))

			rate = self.get_rm_rate({
				"item_code": d.item_code,
				"bom_no": d.bom_no,
				"qty": d.qty,
				"uom": d.uom,
				"stock_uom": d.stock_uom,
				"conversion_factor": d.conversion_factor
			})

			if rate:
				d.rate = rate
			d.amount = flt(d.rate) * flt(d.qty)
			d.base_rate = flt(d.rate) * flt(self.conversion_rate)
			d.base_amount = flt(d.amount) * flt(self.conversion_rate)

			if save:
				d.db_update()

		if self.docstatus == 1:
			self.flags.ignore_validate_update_after_submit = True

		self.calculate_cost()
		if save:
			self.db_update()
			self.notify_update()
		self.update_exploded_items()

		# update parent BOMs
		if self.total_cost != existing_bom_cost and update_parent:
			parent_boms = frappe.db.sql_list("""select distinct parent from `tabBOM Item`
				where bom_no = %s and docstatus=1 and parenttype='BOM'""", self.name)

			for bom in parent_boms:
				frappe.get_doc("BOM", bom).update_cost(from_child_bom=True)

		if not from_child_bom:
			frappe.msgprint(_("Cost Updated"), alert=True)

	def update_parent_cost(self):
		if self.total_cost:
			cost = self.total_cost / self.quantity

			frappe.db.sql("""update `tabBOM Item` set rate=%s, amount=stock_qty*%s
				where bom_no = %s and docstatus < 2 and parenttype='BOM'""",
				(cost, cost, self.name))

	def get_bom_unitcost(self, bom_no):
		bom = frappe.db.sql("""
			select name, base_total_cost/quantity as unit_cost
			from `tabBOM`
			where is_active = 1 and name = %s
		""", bom_no, as_dict=1)

		return bom and bom[0]['unit_cost'] or 0

	def get_valuation_rate(self, args):
		""" Get weighted average of valuation rate from all warehouses """

		total_qty, total_value, valuation_rate = 0.0, 0.0, 0.0
		for d in frappe.db.sql("""select actual_qty, stock_value from `tabBin`
			where item_code=%s""", args['item_code'], as_dict=1):
				total_qty += flt(d.actual_qty)
				total_value += flt(d.stock_value)

		if total_qty:
			valuation_rate =  total_value / total_qty

		if valuation_rate <= 0:
			last_valuation_rate = frappe.db.sql("""select valuation_rate
				from `tabStock Ledger Entry`
				where item_code = %s and valuation_rate > 0
				order by posting_date desc, posting_time desc, creation desc limit 1""", args['item_code'])

			valuation_rate = flt(last_valuation_rate[0][0]) if last_valuation_rate else 0

		if not valuation_rate:
			valuation_rate = frappe.db.get_value("Item", args['item_code'], "valuation_rate")

		return flt(valuation_rate)

	def set_item_operation(self):
		if len(self.operations) != 1:
			return

		operation = self.operations[0].get('operation')
		for d in self.items:
			d.operation = operation

	def manage_default_bom(self):
		""" Uncheck others if current one is selected as default or
			check the current one as default if it the only bom for the selected item,
			update default bom in item master
		"""
		if self.is_default and self.is_active:
			from frappe.model.utils import set_default
			set_default(self, "item")
			item = frappe.get_doc("Item", self.item)
			if item.default_bom != self.name:
				frappe.db.set_value('Item', self.item, 'default_bom', self.name)
		elif not frappe.db.exists(dict(doctype='BOM', docstatus=1, item=self.item, is_default=1)) \
			and self.is_active:
			self.db_set("is_default", 1)
		else:
			self.db_set("is_default", 0)
			item = frappe.get_doc("Item", self.item)
			if item.default_bom == self.name:
				frappe.db.set_value('Item', self.item, 'default_bom', None)

	def validate_main_item(self):
		""" Validate main FG item"""
		item = frappe.get_cached_doc("Item", self.item)
		self.item_name = item.item_name
		self.description = item.description
		self.uom = item.stock_uom

		if not self.quantity:
			frappe.throw(_("Quantity should be greater than 0"))

	def validate_currency(self):
		if self.rm_cost_as_per == 'Price List':
			price_list_currency = frappe.db.get_value('Price List', self.buying_price_list, 'currency')
			if price_list_currency not in (self.currency, self.company_currency()):
				frappe.throw(_("Currency of the price list {0} must be {1} or {2}")
					.format(self.buying_price_list, self.currency, self.company_currency()))

	def update_stock_qty(self):
		for m in self.get('items'):
			if not m.uom and m.stock_uom:
				m.uom = m.stock_uom
				m.qty = m.stock_qty

			if not m.conversion_factor:
				m.conversion_factor = flt(get_conversion_factor(m.item_code, m.uom)['conversion_factor'])

			m.stock_qty = flt(m.conversion_factor)*flt(m.qty)

			m.db_update()

	def validate_uom_is_interger(self):
		from erpnext.utilities.transaction_base import validate_uom_is_integer
		validate_uom_is_integer(self, "uom", "qty", "BOM Item")
		validate_uom_is_integer(self, "stock_uom", "stock_qty", "BOM Item")

	def set_conversion_rate(self):
		if self.currency == self.company_currency():
			self.conversion_rate = 1
		elif self.conversion_rate == 1 or flt(self.conversion_rate) <= 0:
			self.conversion_rate = get_exchange_rate(self.currency, self.company_currency(), args="for_buying")

	def set_plc_conversion_rate(self):
		if self.rm_cost_as_per in ["Valuation Rate", "Last Purchase Rate"]:
			self.plc_conversion_rate = 1
		elif not self.plc_conversion_rate and self.price_list_currency:
			self.plc_conversion_rate = get_exchange_rate(self.price_list_currency,
				self.company_currency(), args="for_buying")

	def validate_materials(self):
		""" Validate raw material entries """

		if not self.get('items'):
			frappe.throw(_("Raw Materials cannot be blank."))

		check_list = []
		for m in self.get('items'):
			if m.bom_no:
				validate_bom_no(m.item_code, m.bom_no)
			if flt(m.qty) <= 0:
				frappe.throw(_("Quantity required for Item {0} in row {1}").format(m.item_code, m.idx))
			check_list.append(m)

	def check_recursion(self, bom_list=[]):
		""" Check whether recursion occurs in any bom"""
		bom_list = self.traverse_tree()
		bom_nos = frappe.get_all('BOM Item', fields=["bom_no"],
			filters={'parent': ('in', bom_list), 'parenttype': 'BOM'})

		raise_exception = False
		if bom_nos and self.name in [d.bom_no for d in bom_nos]:
			raise_exception = True

		if not raise_exception:
			bom_nos = frappe.get_all('BOM Item', fields=["parent"],
				filters={'bom_no': self.name, 'parenttype': 'BOM'})

			if self.name in [d.parent for d in bom_nos]:
				raise_exception = True

		if raise_exception:
			frappe.throw(_("BOM recursion: {0} cannot be parent or child of {1}").format(self.name, self.name))

	def update_cost_and_exploded_items(self, bom_list=[]):
		bom_list = self.traverse_tree(bom_list)
		for bom in bom_list:
			bom_obj = frappe.get_doc("BOM", bom)
			bom_obj.check_recursion(bom_list=bom_list)
			bom_obj.update_exploded_items()

		return bom_list

	def traverse_tree(self, bom_list=None):
		def _get_children(bom_no):
			children = frappe.cache.hget('bom_children', bom_no)
			if children is None:
				children = frappe.db.sql_list("""
					SELECT bom_no FROM `tabBOM Item`
					WHERE parent = %s AND parenttype = 'BOM'
						AND bom_no != '' AND bom_no IS NOT NULL
					ORDER BY idx DESC
				""", bom_no)
				frappe.cache.hset('bom_children', bom_no, children)
			return children

		count = 0
		if not bom_list:
			bom_list = []

		if self.name not in bom_list:
			bom_list.append(self.name)

		while(count < len(bom_list)):
			for child_bom in _get_children(bom_list[count]):
				if child_bom not in bom_list:
					bom_list.append(child_bom)
			count += 1
		bom_list.reverse()
		return bom_list

	def calculate_cost(self):
		"""Calculate bom totals"""
		self.calculate_op_cost()
		self.calculate_rm_cost()
		self.calculate_sm_cost()
		self.total_cost = self.total_operating_cost + self.raw_material_cost - self.scrap_material_cost
		self.base_total_cost = self.base_total_operating_cost + self.base_raw_material_cost - self.base_scrap_material_cost
		self.total_raw_material_qty = sum([flt(d.qty) for d in self.items])
		self.total_raw_material_qty = flt(self.total_raw_material_qty, self.precision("total_raw_material_qty"))

	def calculate_op_cost(self):
		"""Update workstation rate and calculates totals"""
		self.operating_cost = 0
		self.base_operating_cost = 0
		for d in self.get('operations'):
			if d.workstation:
				if not d.hour_rate:
					hour_rate = flt(frappe.db.get_value("Workstation", d.workstation, "hour_rate"))
					d.hour_rate = hour_rate / flt(self.conversion_rate) if self.conversion_rate else hour_rate

			if d.hour_rate and d.time_in_mins:
				d.base_hour_rate = flt(d.hour_rate) * flt(self.conversion_rate)
				d.operating_cost = flt(d.hour_rate) * flt(d.time_in_mins) / 60.0
				d.base_operating_cost = flt(d.operating_cost) * flt(self.conversion_rate)

			self.operating_cost += flt(d.operating_cost)
			self.base_operating_cost += flt(d.base_operating_cost)

		self.additional_operating_cost = 0
		self.base_additional_operating_cost = 0
		for d in self.get('additional_costs'):
			d.base_rate = flt(d.rate) * flt(self.conversion_rate)
			d.amount = flt(flt(d.rate) * flt(self.quantity), d.precision('amount'))
			d.base_amount = flt(d.amount * flt(self.conversion_rate), d.precision('base_amount'))

			self.additional_operating_cost += d.amount
			self.base_additional_operating_cost += d.base_amount

		self.total_operating_cost = self.operating_cost + self.additional_operating_cost
		self.base_total_operating_cost = self.base_operating_cost + self.base_additional_operating_cost

	def calculate_rm_cost(self):
		"""Fetch RM rate as per today's valuation rate and calculate totals"""
		total_rm_cost = 0
		base_total_rm_cost = 0

		for d in self.get('items'):
			d.base_rate = flt(d.rate) * flt(self.conversion_rate)
			d.amount = flt(d.rate) * flt(d.qty)
			d.base_amount = d.amount * flt(self.conversion_rate)
			d.qty_consumed_per_unit = flt(d.stock_qty) \
				/ flt(self.quantity, self.precision("quantity"))

			total_rm_cost += d.amount
			base_total_rm_cost += d.base_amount

		self.raw_material_cost = total_rm_cost
		self.base_raw_material_cost = base_total_rm_cost

	def calculate_sm_cost(self):
		"""Fetch RM rate as per today's valuation rate and calculate totals"""
		total_sm_cost = 0
		base_total_sm_cost = 0

		for d in self.get('scrap_items'):
			d.base_rate = flt(d.rate, d.precision("rate")) * flt(self.conversion_rate, self.precision("conversion_rate"))
			d.amount = flt(d.rate, d.precision("rate")) * flt(d.stock_qty, d.precision("stock_qty"))
			d.base_amount = flt(d.amount, d.precision("amount")) * flt(self.conversion_rate, self.precision("conversion_rate"))
			total_sm_cost += d.amount
			base_total_sm_cost += d.base_amount

		self.scrap_material_cost = total_sm_cost
		self.base_scrap_material_cost = base_total_sm_cost

	def update_new_bom(self, old_bom, new_bom, rate):
		for d in self.get("items"):
			if d.bom_no != old_bom: continue

			d.bom_no = new_bom
			d.rate = rate
			d.amount = (d.stock_qty or d.qty) * rate

	def update_exploded_items(self):
		""" Update Flat BOM, following will be correct data"""
		self.get_exploded_items()
		self.add_exploded_items()

	def get_exploded_items(self):
		""" Get all raw materials including items from child bom"""
		self.cur_exploded_items = {}
		for d in self.get('items'):
			if not d.get("item_code"):
				continue

			if d.bom_no:
				self.get_child_exploded_items(d.bom_no, d.stock_qty, d.skip_transfer_for_manufacture)
			else:
				self.add_to_cur_exploded_items(frappe._dict({
					'item_code': d.item_code,
					'item_name': d.item_name,
					'operation': d.operation,
					'source_warehouse': d.source_warehouse,
					'description': d.description,
					'image': d.image,
					'uom': d.uom,
					'qty': flt(d.qty),
					'stock_uom': d.stock_uom,
					'stock_qty': flt(d.stock_qty),
					'rate': flt(d.base_rate),
					'skip_transfer_for_manufacture': d.skip_transfer_for_manufacture
				}))

	def company_currency(self):
		return erpnext.get_company_currency(self.company)

	def add_to_cur_exploded_items(self, args):
		key = (args.item_code, args.uom)
		if self.cur_exploded_items.get(key):
			self.cur_exploded_items[key]["qty"] += args.qty
			self.cur_exploded_items[key]["stock_qty"] += args.stock_qty
		else:
			self.cur_exploded_items[key] = args

	def get_child_exploded_items(self, bom_no, qty, skip_transfer_for_manufacture=0):
		""" Add all items from Flat BOM of child BOM"""
		# Did not use qty_consumed_per_unit in the query, as it leads to rounding loss
		child_fb_items = frappe.db.sql("""
			SELECT
				bom_item.item_code,
				bom_item.item_name,
				bom_item.description,
				bom_item.source_warehouse,
				bom_item.operation,
				bom_item.uom,
				bom_item.qty,
				bom_item.stock_uom,
				bom_item.stock_qty,
				bom_item.rate,
				bom_item.skip_transfer_for_manufacture,
				bom_item.qty / ifnull(bom.quantity, 1) AS qty_consumed_per_unit
			FROM `tabBOM Explosion Item` bom_item, tabBOM bom
			WHERE
				bom_item.parent = bom.name
				AND bom.name = %s
				AND bom.docstatus = 1
		""", bom_no, as_dict = 1)

		for d in child_fb_items:
			new_qty = d['qty_consumed_per_unit'] * qty
			new_stock_qty = new_qty * ((d['stock_qty'] / d['qty']) or 1)
			self.add_to_cur_exploded_items(frappe._dict({
				'item_code': d['item_code'],
				'item_name': d['item_name'],
				'source_warehouse': d['source_warehouse'],
				'operation': d['operation'],
				'description': d['description'],
				'uom': d['uom'],
				'qty': new_qty,
				'stock_uom': d['stock_uom'],
				'stock_qty': new_stock_qty,
				'rate': flt(d['rate']),
				'skip_transfer_for_manufacture': 1 if cint(skip_transfer_for_manufacture) else d.get('skip_transfer_for_manufacture', 0)
			}))

	def add_exploded_items(self):
		"Add items to Flat BOM table"
		if self.docstatus == 1:
			frappe.db.sql("""delete from `tabBOM Explosion Item` where parent=%s""", self.name)

		self.set('exploded_items', [])

		for d in sorted(self.cur_exploded_items, key=itemgetter(0)):
			ch = self.append('exploded_items', {})
			for i in self.cur_exploded_items[d].keys():
				ch.set(i, self.cur_exploded_items[d][i])
			ch.amount = flt(ch.qty) * flt(ch.rate)
			ch.qty_consumed_per_unit = flt(ch.qty) / flt(self.quantity)
			ch.stock_qty_consumed_per_unit = flt(ch.stock_qty) / flt(self.quantity)
			ch.docstatus = self.docstatus

			if self.docstatus == 1:
				ch.db_insert()

	def validate_bom_links(self):
		if not self.is_active:
			act_pbom = frappe.db.sql("""select distinct bom_item.parent from `tabBOM Item` bom_item
				where bom_item.bom_no = %s and bom_item.docstatus = 1 and bom_item.parenttype='BOM'
				and exists (select * from `tabBOM` where name = bom_item.parent
					and docstatus = 1 and is_active = 1)""", self.name)

			if act_pbom and act_pbom[0][0]:
				frappe.throw(_("Cannot deactivate or cancel BOM as it is linked with other BOMs"))

	def validate_operations(self):
		if self.with_operations:
			if not self.operations:
				frappe.throw(_("Operations cannot be left blank"))

			bom_operations = set()
			for d in self.operations:
				if not d.batch_size or d.batch_size <= 0:
					d.batch_size = 1

				bom_operations.add(d.operation)

			for d in self.items:
				if d.operation and d.operation not in bom_operations:
					frappe.throw(_("Row #{0}: Item Operation {1} not in BOM Operations")
						.format(d.idx, d.operation))

		else:
			self.set('operations', [])
			for d in self.items:
				d.operation = None


def get_bom_items_as_dict(
	bom,
	company,
	qty=1,
	fetch_exploded=1,
	fetch_scrap_items=0,
	include_non_stock_items=False,
	fetch_qty_in_stock_uom=True
):
	items_dict = {}

	# Did not use qty_consumed_per_unit in the query, as it leads to rounding loss
	query = """
		SELECT
			bom_item.item_code,
			item.item_name,
			sum(bom_item.{qty_field}/ifnull(bom.quantity, 1)) * %(qty)s as qty,
			bom_item.idx,
			item.image,
			bom.project,
			item.has_batch_no,
			item.stock_uom
			{select_columns}
		FROM `tab{table}` bom_item
		INNER JOIN `tabBOM` bom ON bom_item.parent = bom.name
		INNER JOIN `tabItem` item ON item.name = bom_item.item_code
		WHERE
			bom_item.docstatus < 2
			and bom.name = %(bom)s
			and item.is_stock_item in (1, {is_stock_item})
			{where_conditions}
		GROUP BY item_code, stock_uom
		ORDER BY idx
	"""

	is_stock_item = 0 if include_non_stock_items else 1
	if cint(fetch_exploded):
		uom_fields = ""
		if not fetch_qty_in_stock_uom:
			uom_fields = ", bom_item.stock_qty / bom_item.qty as conversion_factor, bom_item.uom"

		query = query.format(
			table="BOM Explosion Item",
			where_conditions="",
			is_stock_item=is_stock_item,
			qty_field="stock_qty" if fetch_qty_in_stock_uom else "qty",
			select_columns=""", bom_item.source_warehouse, bom_item.operation,
				bom_item.skip_transfer_for_manufacture, bom_item.description, bom_item.rate,
				(Select idx from `tabBOM Item` where item_code = bom_item.item_code and parent = %(parent)s limit 1) as idx
				{0}""".format(uom_fields)
		)
		items = frappe.db.sql(query, { "parent": bom, "qty": qty, "bom": bom, "company": company }, as_dict=True)
	elif fetch_scrap_items:
		query = query.format(
			table="BOM Scrap Item",
			where_conditions="",
			select_columns=", bom_item.idx, item.description",
			is_stock_item=is_stock_item,
			qty_field="stock_qty"
		)
		items = frappe.db.sql(query, { "qty": qty, "bom": bom, "company": company }, as_dict=True)
	else:
		query = query.format(
			table="BOM Item",
			where_conditions="",
			is_stock_item=is_stock_item,
			qty_field="stock_qty" if fetch_qty_in_stock_uom else "qty",
			select_columns = """, bom_item.uom, bom_item.conversion_factor, bom_item.source_warehouse,
				bom_item.idx, bom_item.operation, bom_item.skip_transfer_for_manufacture,
				bom_item.description, bom_item.base_rate as rate """
		)
		items = frappe.db.sql(query, { "qty": qty, "bom": bom, "company": company }, as_dict=True)

	# Create dict
	for item in items:
		if item.item_code in items_dict:
			items_dict[item.item_code]["qty"] += flt(item.qty)
		else:
			items_dict[item.item_code] = item

	# Set additional values
	for item, item_details in items_dict.items():
		item_doc = frappe.get_cached_doc("Item", item)
		defaults_args = frappe._dict({"company": company})

		item_details.default_warehouse = get_default_warehouse(item_doc, defaults_args)
		item_details.cost_center = get_default_cost_center(item_doc, defaults_args)

	return items_dict


@frappe.whitelist()
def get_bom_items(bom, company, qty=1, fetch_exploded=1):
	items = get_bom_items_as_dict(bom, company, qty, fetch_exploded, include_non_stock_items=True).values()
	items = list(items)
	items.sort(key = functools.cmp_to_key(lambda a, b: a.item_code > b.item_code and 1 or -1))
	return items


def validate_bom_no(item, bom_no):
	"""Validate BOM No of subcontracted items"""
	bom = frappe.get_doc("BOM", bom_no)

	if not bom.is_active:
		frappe.throw(_("BOM {0} must be active").format(bom_no))

	if bom.docstatus != 1:
		if not getattr(frappe.flags, "in_test", False):
			frappe.throw(_("BOM {0} must be submitted").format(bom_no))

	if item:
		rm_item_exists = False
		for d in bom.items:
			if d.item_code.lower() == item.lower():
				rm_item_exists = True

		for d in bom.scrap_items:
			if d.item_code.lower() == item.lower():
				rm_item_exists = True

		if bom.item.lower() == item.lower() or bom.item.lower() == cstr(frappe.get_cached_value("Item", item, "variant_of")).lower():
			rm_item_exists = True

		if not rm_item_exists:
			frappe.throw(_("BOM {0} does not belong to Item {1}").format(bom_no, item))


@frappe.whitelist()
def get_children(doctype, parent=None, is_root=False, **filters):
	if not parent or parent=="BOM":
		frappe.msgprint(_('Please select a BOM'))
		return

	if parent:
		frappe.form_dict.parent = parent

	if frappe.form_dict.parent:
		bom_doc = frappe.get_doc("BOM", frappe.form_dict.parent)
		frappe.has_permission("BOM", doc=bom_doc, throw=True)

		bom_items = frappe.get_all('BOM Item',
			fields=['item_code', 'bom_no as value', 'stock_qty'],
			filters=[['parent', '=', frappe.form_dict.parent]],
			order_by='idx')

		item_names = tuple(d.get('item_code') for d in bom_items)

		items = frappe.get_list('Item',
			fields=['image', 'description', 'name', 'stock_uom', 'item_name'],
			filters=[['name', 'in', item_names]]) # to get only required item dicts

		for bom_item in bom_items:
			# extend bom_item dict with respective item dict
			bom_item.update(
				# returns an item dict from items list which matches with item_code
				next(item for item in items if item.get('name')
					== bom_item.get('item_code'))
			)

			bom_item.parent_bom_qty = bom_doc.quantity
			bom_item.expandable = 0 if bom_item.value in ('', None)  else 1

		return bom_items


def get_boms_in_bottom_up_order(bom_no=None):
	from erpnext.manufacturing.doctype.bom.bom_tree import BOMGraph

	bom_nos = frappe.db.sql_list("""
		select name
		from `tabBOM`
		where docstatus = 1 and is_active = 1
	""")

	bom_edges = frappe.db.sql("""
		select bom.name as parent, i.bom_no as child
		from `tabBOM Item` i
		inner join `tabBOM` bom on bom.name = i.parent
		where ifnull(i.bom_no, '') != '' and bom.docstatus = 1 and bom.is_active = 1
	""", as_dict=1)

	bom_graph = BOMGraph(bom_nos)
	for d in bom_edges:
		bom_graph.add_edge(d.parent, d.child)

	sorted_boms = bom_graph.topological_sort(parent_bom=bom_no)

	return sorted_boms


def add_additional_cost(stock_entry, work_order):
	# Add non stock items cost in the additional cost
	stock_entry.additional_costs = []
	company = work_order.company or stock_entry.company
	expenses_included_in_valuation = frappe.get_cached_value("Company", company, "expenses_included_in_valuation")

	add_non_stock_items_cost(stock_entry, work_order, expenses_included_in_valuation)
	add_operations_cost(stock_entry, work_order, expenses_included_in_valuation)


def add_non_stock_items_cost(stock_entry, work_order, expense_account):
	bom_no = work_order.bom_no or stock_entry.bom_no
	use_multi_level_bom = work_order.get('use_multi_level_bom') or stock_entry.get('use_multi_level_bom')
	bom = frappe.get_doc('BOM', bom_no)
	table = 'exploded_items' if use_multi_level_bom else 'items'

	items = {}
	for d in bom.get(table):
		items.setdefault(d.item_code, d.amount)

	non_stock_items = frappe.get_all('Item',
		fields="name", filters={'name': ('in', list(items.keys())), 'ifnull(is_stock_item, 0)': 0}, as_list=1)

	non_stock_items_cost = 0.0
	for name in non_stock_items:
		non_stock_items_cost += flt(items.get(name[0])) * flt(stock_entry.fg_completed_qty) / flt(bom.quantity)

	if non_stock_items_cost:
		stock_entry.append('additional_costs', {
			'expense_account': expense_account,
			'description': _("Non stock items"),
			'amount': non_stock_items_cost
		})


def add_operations_cost(stock_entry, work_order=None, expense_account=None):
	from erpnext.stock.doctype.stock_entry.stock_entry import get_operating_cost_per_unit, get_additional_operating_costs

	operating_cost_per_unit = get_operating_cost_per_unit(work_order, stock_entry.bom_no)
	if operating_cost_per_unit:
		stock_entry.append('additional_costs', {
			"expense_account": expense_account,
			"description": _("Operating Cost as per Work Order / BOM"),
			"amount": operating_cost_per_unit * flt(stock_entry.fg_completed_qty)
		})

	additional_costs = get_additional_operating_costs(work_order, stock_entry.bom_no, stock_entry.use_multi_level_bom)
	for d in additional_costs:
		stock_entry.append('additional_costs', {
			"expense_account": d.expense_account or expense_account,
			"description": d.description or _("Additional Operating Cost"),
			"amount": flt(d.rate) * flt(stock_entry.fg_completed_qty)
		})


def get_additional_operating_cost_per_unit(bom_no, use_multi_level_bom=0, bom_list=None):
	if not bom_list:
		if use_multi_level_bom:
			bom_list = frappe.get_doc("BOM", bom_no).traverse_tree()
		else:
			bom_list = [bom_no]

	additional_costs = frappe.db.sql("""
		select description, expense_account, sum(base_amount) as total_amount
		from `tabBOM Additional Cost`
		where parent in %s
		group by description, expense_account
	""", [bom_list], as_dict=1)

	bom_qty = flt(frappe.db.get_value("BOM", bom_no, "quantity", cache=1))
	for d in additional_costs:
		d.rate = d.total_amount / bom_qty if bom_qty else 0

	return additional_costs


@frappe.whitelist()
def get_bom_diff(bom1, bom2):
	from frappe.model import table_fields

	if bom1 == bom2:
		frappe.throw(_("BOM 1 {0} and BOM 2 {1} should not be same")
			.format(frappe.bold(bom1), frappe.bold(bom2)))

	doc1 = frappe.get_doc('BOM', bom1)
	doc2 = frappe.get_doc('BOM', bom2)

	out = get_diff(doc1, doc2)
	out.row_changed = []
	out.added = []
	out.removed = []

	meta = doc1.meta

	identifiers = {
		'operations': 'operation',
		'items': 'item_code',
		'scrap_items': 'item_code',
		'exploded_items': 'item_code'
	}

	for df in meta.fields:
		old_value, new_value = doc1.get(df.fieldname), doc2.get(df.fieldname)

		if df.fieldtype in table_fields:
			identifier = identifiers[df.fieldname]
			# make maps
			old_row_by_identifier, new_row_by_identifier = {}, {}
			for d in old_value:
				old_row_by_identifier[d.get(identifier)] = d
			for d in new_value:
				new_row_by_identifier[d.get(identifier)] = d

			# check rows for additions, changes
			for i, d in enumerate(new_value):
				if d.get(identifier) in old_row_by_identifier:
					diff = get_diff(old_row_by_identifier[d.get(identifier)], d, for_child=True)
					if diff and diff.changed:
						out.row_changed.append((df.fieldname, i, d.get(identifier), diff.changed))
				else:
					out.added.append([df.fieldname, d.as_dict()])

			# check for deletions
			for d in old_value:
				if not d.get(identifier) in new_row_by_identifier:
					out.removed.append([df.fieldname, d.as_dict()])

	return out
