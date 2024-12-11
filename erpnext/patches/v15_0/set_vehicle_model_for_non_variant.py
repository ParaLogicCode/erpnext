import frappe
from erpnext.vehicles.doctype.vehicle.vehicle import get_vehicle_make_model


def execute():
	vehicles = frappe.db.sql_list("""
		select v.name
		from `tabVehicle` v
		where v.variant_of = '' or v.variant_of is null
	""")

	for name in vehicles:
		doc = frappe.get_doc("Vehicle", name)
		doc.update(get_vehicle_make_model(doc.item_code))
		doc.db_set({
			"variant_of": doc.variant_of,
			"variant_of_name": doc.variant_of_name,
		}, update_modified=False)
		doc.clear_cache()
