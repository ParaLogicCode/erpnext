// Copyright (c) 2024, ParaLogic and contributors
// For license information, please see license.txt

frappe.provide("erpnext");

erpnext.VehicleController = class VehicleController extends frappe.ui.form.Controller {
	setup() {
		this.setup_queries();
	}

	refresh() {
		erpnext.hide_company();
		this.setup_buttons();
		this.set_cant_change_read_only();
		this.render_maintenance_schedules();
	}

	setup_queries() {
		this.frm.set_query("brand", () => erpnext.queries.vehicle_brand());

		this.frm.set_query("variant_of", () => {
			let filters = {"is_vehicle": 1, "is_model": 1};
			if (this.frm.doc.brand) {
				filters.brand = this.frm.doc.brand;
			}
			return erpnext.queries.item(filters);
		});

		this.frm.set_query("item_code", () => {
			let filters = {"is_vehicle": 1, "is_variant": 1};
			if (this.frm.doc.variant_of) {
				filters.variant_of = this.frm.doc.variant_of;
			} else if (this.frm.doc.brand) {
				filters.brand = this.frm.doc.brand;
			}
			return erpnext.queries.item(filters);
		});

		this.frm.set_query("sales_order", () => {
			return {
				filters: {'docstatus': ['!=', 2]}
			};
		});

		this.frm.set_query("insurance_company", () => {
			return {
				query: "erpnext.controllers.queries.customer_query",
				filters: {is_insurance_company: 1}
			};
		});

		this.frm.set_query("vehicle_owner", () => {
			return erpnext.queries.customer();
		});

		this.frm.set_query("reserved_customer", () => {
			return erpnext.queries.customer();
		});

		this.frm.set_query("color", () => {
			return erpnext.queries.vehicle_color({item_code: this.frm.doc.item_code});
		});

		this.frm.set_query("interior", () => {
			return erpnext.queries.vehicle_interior({item_code: this.frm.doc.item_code});
		});
	}

	setup_buttons() {
		if(!this.frm.is_new()) {
			this.frm.add_custom_button(__("View Ledger"), () => {
				frappe.route_options = {
					serial_no: this.frm.doc.name,
					from_date: frappe.defaults.get_user_default("year_start_date"),
					to_date: frappe.defaults.get_user_default("year_end_date")
				};
				frappe.set_route("query-report", "Stock Ledger");
			});
		}
	}

	set_cant_change_read_only() {
		const cant_change_fields = (this.frm.doc.__onload && this.frm.doc.__onload.cant_change_fields) || {};
		$.each(cant_change_fields, (fieldname, cant_change) => {
			this.frm.set_df_property(fieldname, 'read_only', cant_change ? 1 : 0);
		});
	}

	item_code() {
		this.set_image();
		if (this.frm.doc.item_code) {
			erpnext.utils.get_vehicle_make_model(this.frm.doc.item_code, (r) => {
				if (r.message) {
					for (let [k, v] of Object.entries(r.message)) {
						this.frm.doc[k] = v;
					}
					this.frm.refresh_fields();
				}
			});
		}
	}

	variant_of() {
		this.frm.doc.item_code = null;
		this.frm.doc.item_name = null;
		this.frm.refresh_fields();
	}

	brand() {
		this.frm.doc.variant_of = null;
		this.frm.doc.variant_of_name = null;
		this.frm.doc.item_code = null;
		this.frm.doc.item_name = null;
		this.frm.refresh_fields();
	}

	set_image() {
		let me = this;
		if (me.frm.doc.item_code) {
			frappe.call({
				method: "erpnext.vehicles.doctype.vehicle.vehicle.get_vehicle_image",
				args: {
					item_code: me.frm.doc.item_code,
				},
				callback: function (r) {
					if (!r.exc) {
						me.frm.set_value('image', r.message);
					}
				}
			})
		}
	}

	unregistered() {
		if (this.frm.doc.unregistered) {
			this.frm.set_value("license_plate", "");
			this.frm.set_value("plate_region", null);
		}
	}

	chassis_no() {
		erpnext.utils.format_vehicle_id(this.frm, 'chassis_no');
		erpnext.utils.validate_duplicate_vehicle(this.frm.doc, 'chassis_no');
	}
	engine_no() {
		erpnext.utils.format_vehicle_id(this.frm, 'engine_no');
		erpnext.utils.validate_duplicate_vehicle(this.frm.doc, 'engine_no');
	}
	license_plate() {
		erpnext.utils.format_vehicle_id(this.frm, 'license_plate');
		erpnext.utils.validate_duplicate_vehicle(this.frm.doc, 'license_plate');
	}

	plate_region() {
		if (this.frm.doc.plate_region) {
			return erpnext.utils.get_license_plate_with_prefix(
				this.frm.doc.plate_region,
				this.frm.doc.license_plate,
				(license_plate) => this.frm.set_value("license_plate", license_plate)
			);
		}
	}

	render_maintenance_schedules() {
		if (this.frm.fields_dict.maintenance_schedule_html && !this.frm.doc.__islocal) {
			var wrapper = this.frm.fields_dict.maintenance_schedule_html.wrapper;
			$(wrapper).html(frappe.render_template("maintenance_schedule", { data: this.frm.doc}));
		}
	}
};

extend_cscript(cur_frm.cscript, new erpnext.VehicleController({frm: cur_frm}));
