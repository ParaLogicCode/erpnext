# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
from frappe.model.document import Document
from erpnext.utilities.transaction_base import validate_uom_is_integer
from erpnext.setup.doctype.uom_conversion_factor.uom_conversion_factor import get_uom_conv_factor


class PackageType(Document):
	def validate(self):
		self.validate_items()
		validate_uom_is_integer(self, "stock_uom", "stock_qty")
		validate_uom_is_integer(self, "uom", "qty")
		self.calculate_totals()
		self.calculate_volume()
		self.validate_weights()

	def validate_items(self):
		for d in self.get("packaging_items"):
			if d.item_code:
				if frappe.get_cached_value("Item", d.item_code, "has_variants"):
					frappe.throw(_("Row #{0}: {1} is a template Item, please select one of its variants")
						.format(d.idx, frappe.bold(d.item_code)))

				if not frappe.get_cached_value("Item", d.item_code, "is_stock_item"):
					frappe.throw(_("Row #{0}: {1} is not a stock Item")
						.format(d.idx, frappe.bold(d.item_code)))

				if flt(d.qty) <= 0:
					frappe.throw(_("Row #{0}: Item {1}, quantity must be positive number")
						.format(d.idx, frappe.bold(d.item_code)))

	def validate_weights(self):
		for d in self.get("packaging_items"):
			if flt(d.tare_weight) < 0:
				frappe.throw(_("Row #{0}: {1} cannot be negative").format(d.idx, d.meta.get_label('tare_weight')))

		if flt(self.total_tare_weight) < 0:
			frappe.throw(_("Total Tare Weight cannot be negative"))

	def calculate_totals(self):
		self.total_tare_weight = 0

		for item in self.get("packaging_items"):
			self.round_floats_in(item, excluding=['tare_weight_per_unit'])
			item.stock_qty = flt(item.qty * item.conversion_factor, 6)
			item.tare_weight = flt(item.tare_weight_per_unit * item.stock_qty, item.precision("tare_weight"))

			self.total_tare_weight += item.tare_weight

		self.round_floats_in(self, ['total_tare_weight'])

	def calculate_volume(self):
		if self.volume_based_on == "Dimensions":
			self.round_floats_in(self, ['length', 'width', 'height'])
			self.volume = flt(self.length * self.width * self.height, self.precision("volume"))
		else:
			self.length = 0
			self.width = 0
			self.height = 0


def get_package_type_tare_weight(package_type, weight_uom=None):
	package_type_doc = frappe.get_cached_doc("Package Type", package_type)
	if not weight_uom:
		weight_uom = package_type_doc.weight_uom

	tare_weight = flt(package_type_doc.total_tare_weight)

	if weight_uom and weight_uom != package_type_doc.weight_uom:
		conversion_factor = flt(get_uom_conv_factor(package_type_doc.weight_uom, weight_uom))
		tare_weight *= conversion_factor

	return tare_weight
