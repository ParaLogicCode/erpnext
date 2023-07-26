// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

{% include 'erpnext/selling/sales_common.js' %}
{% include 'erpnext/selling/quotation_common.js' %}

frappe.provide("erpnext.crm");

erpnext.crm.Opportunity = class Opportunity extends frappe.ui.form.Controller {
	setup() {
		this.frm.custom_make_buttons = {
			'Customer': 'Customer',
			'Quotation': 'Quotation',
			'Appointment': 'Appointment',
			'Vehicle Quotation': 'Vehicle Quotation',
			'Vehicle Booking Order': 'Vehicle Booking Order',
			'Vehicle Gate Pass': 'Test Drive Gate Pass',
			'Supplier Quotation': 'Supplier Quotation',
		};

		erpnext.setup_applies_to_fields(this.frm);

		this.frm.email_field = 'contact_email';
		this.setup_queries();
	}

	refresh() {
		erpnext.hide_company();
		erpnext.toggle_naming_series();

		this.set_dynamic_link();
		this.update_dynamic_fields();
		this.set_sales_person_from_user();
		this.setup_buttons();
		this.setup_dashboard();
	}

	onload_post_render() {
		this.frm.get_field("items").grid.set_multiple_add("item_code", "qty");
	}

	setup_buttons() {
		var me = this;
		me.setup_notification_buttons();

		if (!me.frm.doc.__islocal) {
			if(me.frm.perm[0].write) {
				me.frm.add_custom_button(__('Schedule Follow Up'), () => me.schedule_follow_up(),
					__("Communication"));

				me.frm.add_custom_button(__('Submit Communication'), () => me.submit_communication(),
					__("Communication"));

				if (!["Lost", "Closed", "Converted"].includes(me.frm.doc.status)) {
					me.frm.add_custom_button(__("Lost"), () => {
						me.frm.events.set_as_lost_dialog(me.frm);
					}, __("Status"));

					me.frm.add_custom_button(__("Close"), () => {
						me.frm.set_value("status", "Closed");
						me.frm.save();
					}, __("Status"));
				}

				if (["Lost", "Closed"].includes(me.frm.doc.status)) {
					me.frm.add_custom_button(__("Reopen"), () => {
						if (me.frm.doc.status == "Lost") {
							me.frm.events.update_lost_status(me.frm, false);
						} else {
							me.frm.set_value("lost_reasons", [])
							me.frm.set_value("order_lost_reason", null)
							me.frm.set_value("status", "Open");
							me.frm.save();
						}
					}, __("Status"));
				}
			}

			if(me.frm.doc.status !== "Lost") {
				if (!me.frm.doc.__onload.customer) {
					me.frm.add_custom_button(__('Customer'), () => me.create_customer(),
						__('Create'));
				}

				if (frappe.boot.active_domains.includes("Vehicles") && (!me.frm.doc.conversion_document || me.frm.doc.conversion_document == "Order")) {
					me.frm.add_custom_button(__("Vehicle Booking Order"), () => me.make_vehicle_booking_order(),
						__('Create'));

					me.frm.add_custom_button(__("Vehicle Quotation"), () => me.make_vehicle_quotation(),
						__('Create'));

					me.frm.add_custom_button(__("Test Drive Gate Pass"), () => me.make_opportunity_gate_pass(),
						__('Create'));
				}

				me.frm.add_custom_button(__('Quotation'), () => me.create_quotation(),
					__('Create'));

				if (!me.frm.doc.conversion_document || me.frm.doc.conversion_document == "Appointment") {
					me.frm.add_custom_button(__('Appointment'), () => me.create_appointment(),
						__('Create'));
				}

				if (me.frm.doc.items && me.frm.doc.items.length) {
					me.frm.add_custom_button(__('Supplier Quotation'), () => me.make_supplier_quotation(),
						__('Create'));
				}

				me.frm.page.set_inner_btn_group_as_primary(__("Create"));
			}
		}
	}

	setup_notification_buttons() {
		if(this.frm.is_new()) {
			return
		}

		if (this.can_notify("Opportunity Greeting")) {
			var confirmation_count = frappe.get_notification_count(this.frm, 'Opportunity Greeting', 'SMS');
			let label = __("Opportunity Greeting{0}", [confirmation_count ? " (Resend)" : ""]);
			this.frm.add_custom_button(label, () => this.send_sms('Opportunity Greeting'),
				__("Notify"));
		}

		this.frm.add_custom_button(__("Custom Message"), () => this.send_sms('Custom Message'),
			__("Notify"));
	}


	setup_queries() {
		var me = this;

		me.frm.set_query("opportunity_from", function() {
			return {
				"filters": {
					"name": ["in", ["Customer", "Lead"]],
				}
			}
		});

		me.frm.set_query('party_name', function() {
			if (me.frm.doc.appointment_for === "Customer") {
				return erpnext.queries.customer();
			} else if (me.frm.doc.appointment_for === "Lead") {
				return erpnext.queries.lead();
			}
		});

		me.frm.set_query('customer_address', erpnext.queries.address_query);
		me.frm.set_query('contact_person', erpnext.queries.contact_query);

		me.frm.set_query("item_code", "items", function() {
			return {
				query: "erpnext.controllers.queries.item_query",
				filters: {'is_sales_item': 1}
			};
		});

		if (me.frm.fields_dict.delivery_period) {
			me.frm.set_query("delivery_period", function () {
				if (me.frm.doc.transaction_date) {
					return {
						filters: {to_date: [">=", me.frm.doc.transaction_date]}
					}
				}
			});
		}
	}

	setup_dashboard() {
		if (this.frm.is_new()) {
			return
		}

		this.frm.dashboard.stats_area_row.empty();

		var reminder_count = frappe.get_notification_count(this.frm, 'Opportunity Greeting', 'SMS');
		var reminder_status = reminder_count ? __("{0} SMS", [reminder_count]) : __("Not Sent");
		var reminder_color = reminder_count ? "green"
			: this.can_notify('Opportunity Greeting') ? "yellow" : "grey";

		this.frm.dashboard.add_indicator(__('Opportunity Greeting: {0}', [reminder_status]), reminder_color);
	}

	update_dynamic_fields() {
		var me = this;

		if (me.frm.doc.opportunity_from) {
			me.frm.set_df_property("party_name", "label", __(me.frm.doc.opportunity_from));
			me.frm.set_df_property("customer_address", "label", __(me.frm.doc.opportunity_from + " Address"));
			me.frm.set_df_property("contact_person", "label", __(me.frm.doc.opportunity_from + " Contact Person"));
		} else {
			me.frm.set_df_property("party_name", "label", __("Party"));
			me.frm.set_df_property("customer_address", "label", __("Address"));
			me.frm.set_df_property("contact_person", "label", __("Contact Person"));
		}

		var vehicle_sales_fields = [
			"vehicle_sb_1",
			"vehicle_sb_2",
			"feedback_section",
			"ratings_section",
			"previously_owned_section"
		];

		for (let field of vehicle_sales_fields) {
			me.frm.toggle_display(field, me.frm.doc.conversion_document == "Order");
		}

		var vehicle_maintenance_fields = [
			"due_date",
			"applies_to_vehicle",
			"vehicle_license_plate",
			"vehicle_unregistered",
			"vehicle_chassis_no",
			"vehicle_engine_no",
			"vehicle_last_odometer",
		]

		$.each(vehicle_maintenance_fields, function (i, f) {
			if (me.frm.fields_dict[f]) {
				me.frm.set_df_property(f, "hidden", me.frm.doc.conversion_document == "Order" ? 1 : 0);
			}
		});
	}

	set_dynamic_link() {
		var doctype = this.frm.doc.opportunity_from == 'Lead' ? 'Lead' : 'Customer';
		frappe.dynamic_link = {doc: this.frm.doc, fieldname: 'party_name', doctype: doctype}
	}

	set_sales_person_from_user() {
		if (!this.frm.get_field('sales_person') || this.frm.doc.sales_person || !this.frm.doc.__islocal) {
			return;
		}

		erpnext.utils.get_sales_person_from_user(sales_person => {
			if (sales_person) {
				this.frm.set_value('sales_person', sales_person);
			}
		});
	}

	opportunity_from() {
		this.set_dynamic_link();
		this.update_dynamic_fields();
		this.frm.set_value("party_name", "");
	}

	opportunity_type() {
		this.setup_buttons()
		this.update_dynamic_fields()
	}

	contact_person() {
		return erpnext.utils.get_contact_details(this.frm);
	}

	customer_address() {
		erpnext.utils.get_address_display(this.frm, 'customer_address', 'address_display', false);
	}

	party_name() {
		return this.get_customer_details();
	}

	get_customer_details() {
		var me = this;

		if (me.frm.doc.company && me.frm.doc.opportunity_from && me.frm.doc.party_name) {
			return frappe.call({
				method: "erpnext.crm.doctype.opportunity.opportunity.get_customer_details",
				args: {
					args: {
						doctype: me.frm.doc.doctype,
						company: me.frm.doc.company,
						opportunity_from: me.frm.doc.opportunity_from,
						party_name: me.frm.doc.party_name,
					}
				},
				callback: function (r) {
					if (r.message && !r.exc) {
						return me.frm.set_value(r.message);
					}
				}
			});
		}
	}

	item_code(doc, cdt, cdn) {
		var d = frappe.get_doc(cdt, cdn);

		if (d.item_code) {
			return frappe.call({
				method: "erpnext.crm.doctype.opportunity.opportunity.get_item_details",
				args: {
					"item_code": d.item_code
				},
				callback: function(r) {
					if(r.message) {
						$.each(r.message, function(k, v) {
							frappe.model.set_value(cdt, cdn, k, v);
						});
					}
				}
			});
		}
	}

	schedule_follow_up() {
		var me = this;
		me.frm.check_if_unsaved();

		var dialog = new frappe.ui.Dialog({
			title: __('Schedule a Follow Up'),
			doc: {},
			fields: [
				{
					label : "Follow Up in Days",
					fieldname: "follow_up_days",
					fieldtype: "Int",
					default: 0,
					onchange: () => {
						let today = frappe.datetime.nowdate();
						let contact_date = frappe.datetime.add_days(today, dialog.get_value('follow_up_days'));
						dialog.set_value('schedule_date', contact_date);
					}
				},
				{
					fieldtype: "Column Break"
				},
				{
					label : "Schedule Date",
					fieldname: "schedule_date",
					fieldtype: "Date",
					reqd: 1,
					onchange: () => {
						var today = frappe.datetime.get_today();
						var schedule_date = dialog.get_value('schedule_date');
						dialog.doc.follow_up_days = frappe.datetime.get_diff(schedule_date, today);
						dialog.get_field('follow_up_days').refresh();
					}
				},
				{
					fieldtype: "Section Break"
				},
				{
					label : "To Discuss",
					fieldname: "to_discuss",
					fieldtype: "Small Text",
				},
			],
			primary_action: function() {
				var data = dialog.get_values();

				frappe.call({
					method: "erpnext.crm.doctype.opportunity.opportunity.schedule_follow_up",
					args: {
						name: me.frm.doc.name,
						schedule_date: data.schedule_date,
						to_discuss: data.to_discuss || ""
					},
					callback: function (r) {
						if (!r.exc) {
							me.frm.reload_doc();
						}
					}
				});
				dialog.hide();
			},
			primary_action_label: __('Schedule')
		});
		dialog.show();
	}

	submit_communication() {
		var me = this;
		me.frm.check_if_unsaved();

		var row = this.frm.doc.contact_schedule.find(element => !element.contact_date);

		var d = new frappe.ui.Dialog({
			title: __('Submit Communication'),
			fields: [
				{
					"label" : "Schedule Date",
					"fieldname": "schedule_date",
					"fieldtype": "Date",
					"default": row && row.schedule_date,
					"read_only": 1
				},
				{
					fieldtype: "Column Break"
				},
				{
					"label" : "Contact Date",
					"fieldname": "contact_date",
					"fieldtype": "Date",
					"reqd": 1,
					"default": frappe.datetime.nowdate()
				},
				{
					fieldtype: "Section Break"
				},
				{
					"label" : "To Discuss",
					"fieldname": "to_discuss",
					"fieldtype": "Small Text",
					"default": row && row.to_discuss,
					"read_only": 1
				},
				{
					"label" : "Remarks",
					"fieldname": "remarks",
					"fieldtype": "Small Text",
					"reqd": 1
				},
			],
			primary_action: function() {
				var data = d.get_values();

				frappe.call({
					method: "erpnext.crm.doctype.opportunity.opportunity.submit_communication",
					args: {
						opportunity: me.frm.doc.name,
						contact_date: data.contact_date,
						remarks: data.remarks,
					},
					callback: function (r) {
						if (!r.exc) {
							me.frm.reload_doc();
						}
					}
				});
				d.hide();
			},
			primary_action_label: __('Submit')
		});
		d.show();
	}

	create_customer() {
		erpnext.utils.make_customer_from_lead(this.frm, this.frm.doc.party_name);
	}

	create_quotation() {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.opportunity.opportunity.make_quotation",
			frm: this.frm
		});
	}

	make_vehicle_quotation() {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.opportunity.opportunity.make_vehicle_quotation",
			frm: this.frm
		});
	}

	make_opportunity_gate_pass() {
		return frappe.call ({
			method: "erpnext.crm.doctype.opportunity.opportunity.make_opportunity_gate_pass",
			args :{
				"opportunity": this.frm.doc.name,
			},
			callback: function (r){
				if (!r.exc) {
					var doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	}

	make_vehicle_booking_order() {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.opportunity.opportunity.make_vehicle_booking_order",
			frm: this.frm
		});
	}

	create_appointment() {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.opportunity.opportunity.make_appointment",
			frm: this.frm
		});
	}

	make_supplier_quotation() {
		frappe.model.open_mapped_doc({
			method: "erpnext.crm.doctype.opportunity.opportunity.make_supplier_quotation",
			frm: this.frm
		});
	}

	can_notify(what) {
		if (this.frm.doc.__onload && this.frm.doc.__onload.can_notify) {
			return this.frm.doc.__onload.can_notify[what];
		} else {
			return false;
		}
	}

	send_sms(notification_type) {
		new frappe.SMSManager(this.frm.doc, {
			notification_type: notification_type,
			mobile_no: this.frm.doc.contact_mobile || this.frm.doc.contact_phone,
			party_doctype: this.frm.doc.opportunity_from,
			party: this.frm.doc.party_name,
		});
	}
};

extend_cscript(cur_frm.cscript, new erpnext.crm.Opportunity({frm: cur_frm}));
