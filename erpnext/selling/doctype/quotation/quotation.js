// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

{% include 'erpnext/selling/sales_common.js' %}
{% include 'erpnext/selling/quotation_common.js' %}


erpnext.selling.QuotationController = class QuotationController extends erpnext.selling.SellingController {
	setup() {
		super.setup();

		this.frm.custom_make_buttons = {
			'Sales Order': 'Sales Order',
			'Sales Invoice': 'Sales Invoice',
			'Auto Repeat': 'Subscription',
		};

		this.setup_queries();
	}

	refresh() {
		super.refresh();

		this.set_dynamic_link();
		this.set_default_validity();
		this.setup_buttons();
		this.toggle_reqd_lead_customer();
		this.set_dynamic_field_label();
	}

	setup_queries() {
		super.setup_queries();

		var me = this;

		me.frm.set_query("quotation_to", function() {
			return {
				filters: {
					"name": ["in", ["Customer", "Lead"]],
				}
			}
		});

		me.frm.set_query('party_name', function () {
			if (me.frm.doc.quotation_to == "Lead") {
				return crm.queries.lead({"status": ["!=", "Converted"]});
			} else {
				return erpnext.queries.customer();
			}
		});

		me.frm.set_query('customer_address', me.address_query);
		me.frm.set_query('shipping_address_name', me.address_query);
	}

	set_dynamic_link() {
		var doctype = this.frm.doc.quotation_to == 'Lead' ? 'Lead' : 'Customer';
		frappe.dynamic_link = {doc: this.frm.doc, fieldname: 'party_name', doctype: doctype}
	}

	setup_buttons() {
		var me = this;

		if (me.frm.doc.docstatus == 0) {
			me.add_get_latest_price_button();
		}
		if (me.frm.doc.docstatus == 1) {
			me.add_update_price_list_button();
		}

		var customer;
		if (me.frm.doc.quotation_to == "Customer") {
			customer = me.frm.doc.party_name;
		} else if (me.frm.doc.quotation_to == "Lead") {
			customer = me.frm.doc.__onload && me.frm.doc.__onload.customer;
		}

		if(me.frm.doc.docstatus == 1 && me.frm.doc.status !== 'Lost') {
			if (me.frm.doc.status !== "Ordered") {
				me.frm.add_custom_button(__('Mark As Lost'), () => {
					crm.utils.set_as_lost_dialog(me.frm);
				}, __("Status"));
			}

			if (!customer && frappe.model.can_create("Customer")) {
				me.frm.add_custom_button(__('Customer'), () => {
					erpnext.utils.make_customer_from_lead(me.frm, me.frm.doc.party_name);
				}, __('Create'));
			}

			if (!me.frm.doc.valid_till || frappe.datetime.get_diff(me.frm.doc.valid_till, frappe.datetime.get_today()) >= 0) {
				if (frappe.model.can_create("Sales Order")) {
					me.frm.add_custom_button(__('Sales Order'), () => me.make_sales_order(),
						__('Create'));
				}

				if (frappe.model.can_create("Sales Invoice")) {
					me.frm.add_custom_button(__('Sales Invoice'), () => me.make_sales_invoice(),
						__('Create'));
				}
			}

			if (!me.frm.doc.auto_repeat && frappe.model.can_create("Auto Repeat")) {
				me.frm.add_custom_button(__('Subscription'), function() {
					erpnext.utils.make_subscription(me.frm.doc.doctype, me.frm.doc.name)
				}, __('Create'))
			}

			me.frm.page.set_inner_btn_group_as_primary(__('Create'));
		}

		if (me.frm.doc.status == "Lost") {
			me.frm.add_custom_button(__("Re-Open"), () => {
				crm.utils.update_lost_status(me.frm, false);
			}, __("Status"));
		}

		if (me.frm.doc.docstatus === 0 && frappe.model.can_read("Opportunity")) {
			me.frm.add_custom_button(__('Opportunity'),
				function() {
					erpnext.utils.map_current_doc({
						method: "erpnext.overrides.opportunity.opportunity_hooks.make_quotation",
						source_doctype: "Opportunity",
						target: me.frm,
						setters: [
							{
								label: "Customer",
								fieldname: "party_name",
								fieldtype: "Link",
								options: me.frm.doc.quotation_to,
								default: me.frm.doc.party_name || undefined
							},
							{
								label: "Opportunity Type",
								fieldname: "opportunity_type",
								fieldtype: "Link",
								options: "Opportunity Type",
							}
						],
						columns: ['customer_name', 'transaction_date', 'opportunity_type'],
						get_query_filters: {
							status: ["not in", ["Lost", "Closed"]],
							company: me.frm.doc.company
						}
					})
				}, __("Get Items From"), "btn-default");
		}

		if (me.frm.doc.docstatus == 0) {
			me.add_get_applicable_items_button();
			me.add_get_service_template_items_button();
		}
	}

	set_dynamic_field_label() {
		if (this.frm.doc.quotation_to) {
			this.frm.set_df_property("party_name", "label", __(this.frm.doc.quotation_to));
			this.frm.set_df_property("customer_address", "label", __(this.frm.doc.quotation_to + " Address"));
		}
	}

	toggle_reqd_lead_customer() {
		// to overwrite the customer_filter trigger from queries.js
		this.frm.toggle_reqd("party_name", this.frm.doc.quotation_to);
	}

	set_default_validity() {
		if (this.frm.is_new() && !this.frm.doc.valid_till && !cint(this.frm.doc.quotation_validity_days)) {
			if (frappe.boot.sysdefaults.quotation_valid_till) {
				this.frm.set_value('quotation_validity_days', cint(frappe.boot.sysdefaults.quotation_valid_till));
			} else {
				let valid_till = frappe.datetime.add_months(this.frm.doc.transaction_date, 1);
				valid_till = frappe.datetime.add_days(valid_till, -1);
				this.frm.set_value('valid_till', valid_till);
			}
		}
	}

	quotation_to() {
		this.toggle_reqd_lead_customer();
		this.set_dynamic_field_label();
		this.set_dynamic_link();

		this.frm.set_value("party_name", null);
		this.frm.set_value("bill_to", null);
	}

	party_name() {
		if (this.frm.doc.quotation_to == "Customer") {
			return this.frm.set_value("bill_to", this.frm.doc.party_name);
		} else {
			return this.get_party_details();
		}
	}

	bill_to() {
		return this.get_party_details();
	}

	get_party_details() {
		return erpnext.utils.get_party_details(this.frm, null, null, () => {
			this.apply_price_list();
		});
	}

	transaction_date() {
		if (this.frm.doc.transaction_date && cint(this.frm.doc.lead_time_days) > 0) {
			this.frm.trigger('lead_time_days');
		}
		return super.transaction_date();
	}

	delivery_date() {
		if (this.frm.doc.delivery_date) {
			this.set_lead_time_days();
		}
	}

	lead_time_days() {
		if (cint(this.frm.doc.lead_time_days) > 0) {
			let delivery_date = frappe.datetime.add_days(this.frm.doc.transaction_date, cint(this.frm.doc.lead_time_days));
			this.frm.set_value('delivery_date', delivery_date);
		}
	}

	set_lead_time_days() {
		if (this.frm.doc.transaction_date && this.frm.doc.delivery_date) {
			let days = frappe.datetime.get_diff(this.frm.doc.delivery_date, this.frm.doc.transaction_date);
			if (days > 0) {
				this.frm.doc.lead_time_days = days;
				this.frm.refresh_field('lead_time_days');
			}
		}
	}

	tc_name() {
		this.get_terms();
	}

	address_query(doc) {
		return {
			query: 'frappe.contacts.doctype.address.address.address_query',
			filters: {
				link_doctype: frappe.dynamic_link.doctype,
				link_name: doc.party_name
			}
		};
	}

	validate_company_and_party(party_field) {
		if(!this.frm.doc.quotation_to) {
			frappe.msgprint(__("Please select a value for {0} quotation_to {1}", [this.frm.doc.doctype, this.frm.doc.name]));
			return false;
		} else if (this.frm.doc.quotation_to == "Lead") {
			return true;
		} else {
			return super.validate_company_and_party(party_field);
		}
	}

	make_sales_order() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.quotation.quotation.make_sales_order",
			frm: cur_frm
		})
	}

	make_sales_invoice() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.quotation.quotation.make_sales_invoice",
			frm: cur_frm
		})
	}
};

cur_frm.script_manager.make(erpnext.selling.QuotationController);

frappe.ui.form.on("Quotation Item", "stock_balance", function(frm, cdt, cdn) {
	var d = frappe.model.get_doc(cdt, cdn);
	frappe.route_options = {"item_code": d.item_code};
	frappe.set_route("query-report", "Stock Balance");
})
