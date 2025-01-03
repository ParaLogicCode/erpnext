# Copyright (c) 2024, ParaLogic and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cstr
from frappe.model.document import Document
from erpnext.vehicles.utils import format_vehicle_id
import re


class VehiclePlateRegion(Document):
	def validate(self):
		self.clean_prefix()

	def on_change(self):
		clear_region_prefixes_cache()

	def after_rename(self, old_name, new_name, merge):
		clear_region_prefixes_cache()

	def clean_prefix(self):
		self.prefix = format_vehicle_id(self.prefix)

	def validate_license_plate(self, license_plate, item_code=None):
		if not license_plate:
			return

		if self.prefix and not license_plate.startswith(self.prefix):
			frappe.throw(_("Invalid Plate Number {0} for Plate Region {1}. Plate number should include the prefix {2}").format(
				frappe.bold(license_plate), frappe.bold(self.name), frappe.bold(self.prefix)
			))

		license_plate_without_prefix = license_plate
		if self.prefix:
			license_plate_without_prefix = license_plate[len(self.prefix):]

		ignore_regex = False
		if not self.validation_regex:
			ignore_regex = True
		else:
			if self.ignore_regex_for_motor_bike and item_code:
				item_group = frappe.get_cached_value("Item", item_code, "item_group")
				if item_group and frappe.get_cached_value("Item Group", item_group, "is_motor_bike"):
					ignore_regex = True

		if (
			self.validation_regex
			and not ignore_regex
			and not re.match(f"^{self.validation_regex}$", license_plate_without_prefix)
		):
			example_str = ""
			if self.example_plate_number:
				example_str = _("Example Plate Number: {0}").format(self.example_plate_number)

			frappe.throw(_("Invalid Plate Number {0} for Plate Region {1}. {2}").format(
				frappe.bold(license_plate), frappe.bold(self.name), example_str
			))


@frappe.whitelist()
def get_license_plate_with_prefix(vehicle_plate_region, license_plate):
	license_plate = format_vehicle_id(license_plate)

	prefixes = get_region_prefixes()
	for p in prefixes:
		if license_plate.startswith(p):
			license_plate = license_plate[len(p):]
			break

	prefix = cstr(frappe.get_cached_value("Vehicle Plate Region", vehicle_plate_region, "prefix"))
	if prefix:
		license_plate = prefix + license_plate

	return license_plate


def get_region_prefixes():
	def generator():
		prefixes = frappe.get_all("Vehicle Plate Region", pluck="prefix")
		prefixes = [d for d in prefixes if d]
		return prefixes

	return frappe.cache.get_value("vehicle_region_prefixes", generator)


def clear_region_prefixes_cache():
	frappe.cache.delete_value("vehicle_region_prefixes")
