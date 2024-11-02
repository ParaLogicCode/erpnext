// Common UI functionality for applies_to fields

frappe.provide("erpnext");

erpnext.setup_applies_to_fields = function (frm) {
	frappe.ui.form.on(frm.doctype, {
		refresh: function(frm) {
			frm.events.set_applies_to_read_only(frm);

			var vehicle_field = frm.get_docfield("applies_to_vehicle");
			if (vehicle_field) {
				vehicle_field.get_route_options_for_new_doc = function () {
					return {
						"item_code": frm.doc.applies_to_item,
						"item_name": frm.doc.applies_to_item_name,
						"unregistered": frm.doc.vehicle_unregistered,
						"license_plate": frm.doc.vehicle_license_plate,
						"chassis_no": frm.doc.vehicle_chassis_no,
						"engine_no": frm.doc.vehicle_engine_no,
						"color": frm.doc.vehicle_color,
						"warranty_no": frm.doc.vehicle_warranty_no,
						"delivery_date": frm.doc.vehicle_delivery_date,
					}
				}
			}
		},

		onload: function(frm) {
			frm.set_query('vehicle_color', () => {
				return erpnext.queries.vehicle_color({item_code: frm.doc.applies_to_item});
			});
			frm.set_query('vehicle_interior', () => {
				return erpnext.queries.vehicle_interior({item_code: frm.doc.applies_to_item});
			});
		},

		applies_to_vehicle: function (frm) {
			frm.events.set_applies_to_read_only(frm);
			frm.events.get_applies_to_details(frm);
		},

		applies_to_item: function(frm) {
			frm.events.get_applies_to_details(frm);
		},

		set_applies_to_read_only: function(frm) {
			var read_only_fields = [
				'applies_to_item', 'applies_to_item_name',
				'vehicle_license_plate', 'vehicle_unregistered',
				'vehicle_chassis_no', 'vehicle_engine_no',
				'vehicle_color',
				'vehicle_warranty_no', 'vehicle_delivery_date',
			];

			if (frm.doc.doctype != "Project") {
				read_only_fields.push("vehicle_last_odometer");
			}

			$.each(read_only_fields, function (i, f) {
				if (frm.fields_dict[f]) {
					frm.set_df_property(f, "read_only", frm.doc.applies_to_vehicle ? 1 : 0);
				}
			});
		},

		get_applies_to_details: function(frm) {
			var args =  {
				applies_to_item: frm.doc.applies_to_item,
				applies_to_vehicle: frm.doc.applies_to_vehicle,
				doctype: frm.doc.doctype,
				name: frm.doc.name,
			};

			if (frm.doc.doctype != "Project") {
				args.project = frm.doc.project;
			}

			return frappe.call({
				method: "erpnext.stock.get_item_details.get_applies_to_details",
				args: {
					args: args
				},
				callback: function(r) {
					if(!r.exc) {
						return frm.set_value(r.message);
					}
				}
			});
		},

		get_applies_to_vehicle_odometer: function (frm) {
			if (!frm.doc.applies_to_vehicle || !frm.fields_dict.vehicle_last_odometer) {
				return;
			}

			return frappe.call({
				method: "erpnext.stock.get_item_details.get_applies_to_vehicle_odometer",
				args: {
					vehicle: frm.doc.applies_to_vehicle,
					project: frm.doc.project,
				},
				callback: function(r) {
					if(!r.exc) {
						frm.set_value('vehicle_last_odometer', r.message);
					}
				}
			});
		},
	});
}
