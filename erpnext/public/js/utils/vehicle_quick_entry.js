frappe.provide('frappe.ui.form');

frappe.ui.form.VehicleQuickEntryForm = class VehicleQuickEntryForm extends frappe.ui.form.QuickEntryForm {
	init(doctype, after_insert) {
		this.skip_redirect_on_error = true;
		super.init(doctype, after_insert);
	}

	render_dialog() {
		super.render_dialog();
		this.init_post_render_dialog_operations();
	}

	init_post_render_dialog_operations() {
		let me = this;

		let engine_no_field = me.dialog.get_field("engine_no");
		if (engine_no_field) {
			engine_no_field.df.onchange = () => {
				let value = me.dialog.get_value('engine_no');
				value = erpnext.utils.get_formatted_vehicle_id(value);
				me.dialog.doc.engine_no = value;
				me.dialog.get_field('engine_no').refresh();
				erpnext.utils.validate_duplicate_vehicle(me.dialog.doc, "engine_no");
			};
		}

		let chassis_no_field = me.dialog.get_field("chassis_no");
		if (chassis_no_field) {
			chassis_no_field.df.onchange = () => {
				let value = me.dialog.get_value('chassis_no');
				value = erpnext.utils.get_formatted_vehicle_id(value);
				me.dialog.doc.chassis_no = value;
				me.dialog.get_field('chassis_no').refresh();
				erpnext.utils.validate_duplicate_vehicle(me.dialog.doc, "chassis_no");
			};
		}

		let license_plate_field = me.dialog.get_field("license_plate");
		if (license_plate_field) {
			license_plate_field.df.onchange = () => {
				let value = me.dialog.get_value('license_plate');
				value = erpnext.utils.get_formatted_vehicle_id(value);
				me.dialog.doc.license_plate = value;
				me.dialog.get_field('license_plate').refresh();
				erpnext.utils.validate_duplicate_vehicle(me.dialog.doc, "license_plate");
			};
		}

		let plate_region_field = me.dialog.get_field("plate_region");
		if (plate_region_field) {
			plate_region_field.df.onchange = () => {
				let plate_region = me.dialog.get_value('plate_region');
				let license_plate = me.dialog.get_value('license_plate');
				if (plate_region) {
					return erpnext.utils.get_license_plate_with_prefix(
						plate_region,
						license_plate,
						(formatted) => me.dialog.set_value("license_plate", formatted),
					);
				}
			};
		}

		let brand_field = me.dialog.get_field("brand");
		if (brand_field) {
			brand_field.get_query = () => erpnext.queries.vehicle_brand();

			brand_field.df.onchange = () => {
				me.dialog.doc.variant_of = null;
				me.dialog.doc.variant_of_name = null;
				me.dialog.doc.item_code = null;
				me.dialog.doc.item_name = null;
				me.dialog.refresh();
			};
		}

		let variant_of_field = me.dialog.get_field("variant_of");
		if (variant_of_field) {
			variant_of_field.get_query = () => {
				let filters = {"is_vehicle": 1, "is_model": 1};
				if (me.dialog.get_value("brand")) {
					filters.brand = me.dialog.get_value("brand");
				}
				return erpnext.queries.item(filters);
			};

			variant_of_field.df.onchange = () => {
				me.dialog.doc.item_code = null;
				me.dialog.doc.item_name = null;
				me.dialog.refresh();
			};
		}

		let item_code_field = me.dialog.get_field("item_code");
		if (item_code_field) {
			item_code_field.get_query = () => {
				let filters = {"is_vehicle": 1, "is_variant": 1};
				if (me.dialog.get_value("variant_of")) {
					filters.variant_of = me.dialog.get_value("variant_of");
				} else if (me.dialog.get_value("brand")) {
					filters.brand = me.dialog.get_value("brand");
				}
				return erpnext.queries.item(filters);
			}

			item_code_field.df.onchange = () => {
				let item_code = me.dialog.get_value('item_code');
				if (item_code) {
					erpnext.utils.get_vehicle_make_model(item_code, (r) => {
						if (r.message) {
							for (let [k, v] of Object.entries(r.message)) {
								me.dialog.doc[k] = v;
							}
							me.dialog.refresh();
						}
					});
				} else {
					me.dialog.set_value("item_name", null);
				}
			};
			item_code_field.df.onchange();
		}

		let insurance_field = me.dialog.get_field("insurance_company");
		if (insurance_field) {
			insurance_field.get_query = function () {
				return {
					query: "erpnext.controllers.queries.customer_query",
					filters: {
						'is_insurance_company': 1
					}
				}
			}
		}

		let color_field = me.dialog.get_field("color");
		if (color_field) {
			color_field.get_query = function () {
				return erpnext.queries.vehicle_color({item_code: me.dialog.get_value('item_code')});
			}
		}

		let interior_field = me.dialog.get_field("interior");
		if (interior_field) {
			interior_field.get_query = function () {
				return erpnext.queries.vehicle_interior({item_code: me.dialog.get_value('item_code')});
			}
		}
	}
};
