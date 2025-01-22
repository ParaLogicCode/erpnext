// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide('erpnext.projects');

erpnext.projects.ProjectController = class ProjectController extends crm.QuickContacts {
	setup() {
		this.setup_make_methods();
		erpnext.setup_applies_to_fields(this.frm);
		this.frm.add_fetch("service_template", "includes_service_warranty", "includes_service_warranty", "Project Service Template");
	}

	onload() {
		super.onload();
		this.setup_queries();
	}

	refresh() {
		erpnext.hide_company();
		this.set_dynamic_link();
		this.setup_route_options();
		this.setup_naming_series();
		this.setup_buttons();
		this.set_status_read_only();
		this.set_percent_complete_read_only();
		this.set_cant_change_read_only();
		this.set_items_and_totals_html();
		this.set_service_advisor_from_user();
		this.setup_dashboard();
	}

	setup_queries() {
		let me = this;

		me.frm.set_query('customer', 'erpnext.controllers.queries.customer_query');
		me.frm.set_query('bill_to', 'erpnext.controllers.queries.customer_query');

		me.frm.set_query('contact_person', (doc) => {
			me.set_dynamic_link();
			return erpnext.queries.contact_query(doc);
		});
		me.frm.set_query('secondary_contact_person', (doc) => {
			me.set_dynamic_link();
			return erpnext.queries.contact_query(doc);
		});
		me.frm.set_query('billing_contact_person', (doc) => {
			me.set_dynamic_link("bill_to");
			return erpnext.queries.contact_query(doc);
		});

		me.frm.set_query('customer_address', (doc) => {
			me.set_dynamic_link();
			return erpnext.queries.address_query(doc);
		});
		me.frm.set_query('billing_address', (doc) => {
			me.set_dynamic_link("bill_to");
			return erpnext.queries.address_query(doc);
		});

		if (me.frm.fields_dict.insurance_company) {
			me.frm.set_query("insurance_company", function() {
				return {
					query: "erpnext.controllers.queries.customer_query",
					filters: {is_insurance_company: 1}
				};
			});
		}

		// sales order
		me.frm.set_query('sales_order', function () {
			let filters = {
				'project': ["in", me.frm.doc.__islocal ? [""] : [me.frm.doc.name, ""]]
			};

			if (me.frm.doc.customer) {
				filters["customer"] = me.frm.doc.customer;
			}

			return {
				filters: filters
			};
		});

		// depreciation item
		me.frm.set_query('depreciation_item_code', 'non_standard_depreciation', () => erpnext.queries.item());
		me.frm.set_query('underinsurance_item_code', 'non_standard_underinsurance', () => erpnext.queries.item());

		me.frm.set_query("service_template", "service_templates",
			() => erpnext.queries.service_template(me.frm.doc.applies_to_item));

		me.frm.set_query('service_advisor', () => {
			return {
				filters: {
					is_group: 0,
				}
			}
		});

		erpnext.queries.setup_queries(me.frm, "Warehouse", () => {
			return erpnext.queries.warehouse(me.frm.doc);
		});
	}

	set_dynamic_link(customer_field) {
		customer_field = customer_field || "customer";
		frappe.dynamic_link = {doc: this.frm.doc, fieldname: customer_field, doctype: 'Customer'};
	}

	setup_route_options() {
		let me = this;

		let sales_order_field = me.frm.get_docfield("sales_order");
		if (sales_order_field) {
			sales_order_field.get_route_options_for_new_doc = function () {
				if (me.frm.is_new()) return;
				return {
					"customer": me.frm.doc.customer,
					"project": me.frm.doc.name
				};
			};
		}
	}

	setup_naming_series() {
		if (frappe.defaults.get_default("project_naming_by")!="Naming Series") {
			this.frm.toggle_display("naming_series", false);
		} else {
			erpnext.toggle_naming_series();
		}
	}

	setup_make_methods() {
		let me = this;

		me.frm.custom_make_buttons = {
			'Quotation': 'Quotation',
			'Sales Order': 'Sales Order (All)',
			'Delivery Note': 'Delivery Note',
			'Sales Invoice': 'Sales Invoice',
			'Material Request': 'Consumables Request',
			'Stock Entry': 'Consumables Issue',
		};

		let make_method_doctypes = [
			'Maintenance Visit', 'Warranty Claim', 'Quality Inspection', 'Timesheet',
		];

		me.frm.make_methods = {};
		$.each(make_method_doctypes, function (i, dt) {
			me.frm.make_methods[dt] = () => me.open_form(dt);
		});
	}

	setup_buttons() {
		let me = this;

		if (me.frm.doc.status == "Open" && !this.frm.doc.__onload?.cant_change_fields?.["customer"]) {
			me.frm.add_custom_button(__('Select Appointment'), () => {
				me.select_appointment();
			});
		}

		if (!me.frm.is_new()) {
			// Set Status
			if (!me.frm.doc.ready_to_close && !['Cancelled', 'Closed'].includes(me.frm.doc.status)) {
				me.frm.add_custom_button(__('Ready To Close'), () => {
					me.set_project_ready_to_close();
				}, __('Status'));
			}

			if (me.frm.doc.status != 'Open' || (me.frm.doc.__onload && me.frm.doc.__onload.is_manual_project_status)) {
				me.frm.add_custom_button(__('Re-Open'), () => {
					me.reopen_project(false);
				}, __('Status'));
			}

			if (me.frm.doc.__onload && me.frm.doc.__onload.valid_manual_project_status_names) {
				$.each(me.frm.doc.__onload.valid_manual_project_status_names || [], function (i, project_status) {
					if (me.frm.doc.project_status != project_status) {
						me.frm.add_custom_button(__(project_status), () => {
							me.set_project_status(project_status);
						}, __('Status'));
					}
				});
			}

			// Task Buttons
			if (frappe.model.can_read("Task")) {
				// me.frm.add_custom_button(__("Gantt Chart"), function () {
				// 	frappe.route_options = {
				// 		"project": me.frm.doc.name
				// 	};
				// 	frappe.set_route("List", "Task", "Gantt");
				// }, __("Tasks"));
				//
				// me.frm.add_custom_button(__("Kanban Board"), () => {
				// 	frappe.call('erpnext.projects.doctype.project.project.create_kanban_board_if_not_exists', {
				// 		project: me.frm.doc.name
				// 	}).then(() => {
				// 		frappe.set_route('List', 'Task', 'Kanban', me.frm.doc.name);
				// 	});
				// }, __("Tasks"));
			}

			// Create Buttons
			if (frappe.model.can_create("Delivery Note")) {
				me.frm.add_custom_button(__("Delivery Note"), () => me.make_delivery_note(), __("Material"));
			}

			if (frappe.model.can_create("Material Request")) {
				me.frm.add_custom_button(__("Consumables Request"), () => me.make_material_request(), __("Material"));
				me.frm.add_custom_button(__("Consumables Issue"), () => me.make_stock_entry("Material Issue"), __("Material"));
				me.frm.add_custom_button(__("Consumables Return"), () => me.make_stock_entry("Material Receipt"), __("Material"));
			}

			if (frappe.model.can_create("Sales Order")) {
				me.frm.add_custom_button(__("Sales Order (All)"), () => me.make_sales_order(), __("Sales"));
				me.frm.add_custom_button(__("Sales Order (Services)"), () => me.make_sales_order("service"), __("Sales"));
				me.frm.add_custom_button(__("Sales Order (Materials)"), () => me.make_sales_order("stock"), __("Sales"));
			}

			if (frappe.model.can_create("Quotation")) {
				me.frm.add_custom_button(__("Quotation"), () => me.make_quotation(), __("Sales"));
			}

			if (frappe.model.can_create("Sales Invoice")) {
				me.frm.add_custom_button(__("Sales Invoice"), () => me.make_sales_invoice(), __("Sales"));
			}

			if (
				(me.frm.doc.service_templates || []).some(d => d.includes_service_warranty && !d.has_service_warranty)
				&& me.frm.doc.ready_to_close
				&& frappe.model.can_create("Service Warranty")
			) {
				me.frm.add_custom_button(__("Service Warranty"), () => me.create_service_warranties(), __("Sales"));
			}
		}
	}

	setup_dashboard() {
		if (this.frm.doc.__islocal) {
			return;
		}

		let me = this;
		let company_currency = erpnext.get_currency(me.frm.doc.company);

		me.frm.dashboard.stats_area_row.empty();
		me.frm.dashboard.stats_area.show();

		let tasks_status_color;
		if (me.frm.doc.tasks_status == "No Tasks") {
			tasks_status_color = "light-gray";
		} else if (me.frm.doc.tasks_status == "To Assign") {
			tasks_status_color = "orange";
		} else if (me.frm.doc.tasks_status == "Assigned") {
			tasks_status_color = "purple";
		} else if (me.frm.doc.tasks_status == "In Progress") {
			tasks_status_color = "blue";
		} else if (me.frm.doc.tasks_status == "On Hold") {
			tasks_status_color = "red";
		} else if (me.frm.doc.tasks_status == "Completed") {
			tasks_status_color = "green";
		}

		let task_count = "";
		if (me.frm.doc.__onload?.task_count && me.frm.doc.__onload.task_count.total_tasks) {
			task_count = ` (${me.frm.doc.__onload.task_count.completed_tasks}/${me.frm.doc.__onload.task_count.total_tasks})`;
		}

		let delivery_status_color;
		if (me.frm.doc.delivery_status == "Not Applicable") {
			delivery_status_color = "light-gray";
		} else if (me.frm.doc.delivery_status == "Not Delivered") {
			delivery_status_color = "orange";
		} else if (me.frm.doc.delivery_status == "Partly Delivered") {
			delivery_status_color = "yellow";
		} else if (me.frm.doc.delivery_status == "Fully Delivered") {
			delivery_status_color = "green";
		}

		let status_items = [
			{
				contents: __('Tasks Status: {0}{1}', [me.frm.doc.tasks_status, task_count]),
				indicator: tasks_status_color
			},
			{
				contents: __('Material Status: {0}', [me.frm.doc.delivery_status]),
				indicator: delivery_status_color
			},
			{
				contents: __('Ready To Close: {0}', [me.frm.doc.ready_to_close ? __("Yes") : __("No")]),
				indicator: me.frm.doc.ready_to_close ? 'green' : 'orange'
			},
		];

		// Billing Status
		let billing_status_color;
		if (me.frm.doc.billing_status == "Not Applicable") {
			billing_status_color = "light-gray";
		} else if (me.frm.doc.billing_status == "Not Billed") {
			billing_status_color = "orange";
		} else if (me.frm.doc.billing_status == "Partly Billed") {
			billing_status_color = "yellow";
		} else if (me.frm.doc.billing_status == "Fully Billed") {
			billing_status_color = "green";
		}

		let total_billable_color = me.frm.doc.total_billable_amount ? "blue" : "light-gray";
		let customer_billable_color = me.frm.doc.customer_billable_amount ? "blue" : "light-gray";

		let billed_amount_color;
		if (me.frm.doc.total_billed_amount) {
			if (me.frm.doc.total_billed_amount < me.frm.doc.total_billable_amount) {
				billed_amount_color = 'yellow';
			} else if (me.frm.doc.total_billed_amount > me.frm.doc.total_billable_amount) {
				billed_amount_color = 'purple';
			} else {
				billed_amount_color = 'green';
			}
		} else {
			if (me.frm.doc.total_billable_amount) {
				billed_amount_color = 'orange';
			} else {
				billed_amount_color = 'light-gray';
			}
		}

		let billing_items = [
			{
				contents: __('Billing Status: {0}', [me.frm.doc.billing_status]),
				indicator: billing_status_color
			},
		]

		if (me.frm.fields_dict.total_billable_amount && me.frm.fields_dict.total_billable_amount.disp_status != "None") {
			billing_items.push({
				contents: __('Total Billable: {0}', [format_currency(me.frm.doc.total_billable_amount, company_currency)]),
				indicator: total_billable_color
			});
		}


		if (me.frm.fields_dict.customer_billable_amount && me.frm.fields_dict.customer_billable_amount.disp_status != "None") {
			billing_items.push({
				contents: __('Customer Billable: {0}', [format_currency(me.frm.doc.customer_billable_amount, company_currency)]),
				indicator: customer_billable_color
			});
		}

		if (me.frm.fields_dict.total_billed_amount && me.frm.fields_dict.total_billed_amount.disp_status != "None") {
			billing_items.push({
				contents: __('Billed Amount: {0}', [format_currency(me.frm.doc.total_billed_amount, company_currency)]),
				indicator: billed_amount_color
			});
		}

		me.extend_dashboard_items(status_items, billing_items);

		me.add_indicator_section(__("Status"), status_items);
		me.add_indicator_section(__("Billing"), billing_items);
	}

	extend_dashboard_items(status_items, billing_items) { }

	add_indicator_section(title, items) {
		let items_html = '';
		$.each(items || [], function (i, d) {
			items_html += `<span class="indicator ${d.indicator}">${d.contents}</span>`
		});

		let html = $(`<div class="flex-column col-sm-4 col-md-4">
			<div><h5>${title}</h5></div>
			${items_html}
		</div>`);

		html.appendTo(this.frm.dashboard.stats_area_row);

		return html
	}

	set_cant_change_read_only() {
		const cant_change_fields = (this.frm.doc.__onload && this.frm.doc.__onload.cant_change_fields) || {};
		$.each(cant_change_fields, (fieldname, cant_change) => {
			this.frm.set_df_property(fieldname, 'read_only', cant_change ? 1 : 0);
		});
	}

	set_project_status(project_status) {
		let me = this;

		me.frm.check_if_unsaved();
		frappe.confirm(__('Set status as <b>{0}</b>?', [project_status]), () => {
			frappe.xcall('erpnext.projects.doctype.project.project.set_project_status',
				{project: me.frm.doc.name, project_status: project_status}).then(() => me.frm.reload_doc());
		});
	}

	set_project_ready_to_close() {
		let me = this;

		me.frm.check_if_unsaved();
		frappe.confirm(__('Are you sure you want to mark this Project as <b>Ready To Close</b>?'), () => {
			frappe.xcall('erpnext.projects.doctype.project.project.set_project_ready_to_close',
				{project: me.frm.doc.name}).then(() => me.frm.reload_doc());
		});
	}

	reopen_project() {
		let me = this;

		me.frm.check_if_unsaved();
		frappe.confirm(__('Are you sure you want to <b>Re-Open</b> this Project?'), () => {
			frappe.xcall('erpnext.projects.doctype.project.project.reopen_project_status',
				{project: me.frm.doc.name}).then(() => me.frm.reload_doc());
		});
	}

	customer() {
		this.get_customer_details();
	}

	get_customer_details() {
		let me = this;

		return frappe.call({
			method: "erpnext.projects.doctype.project.project.get_customer_details",
			args: {
				args: {
					doctype: me.frm.doc.doctype,
					company: me.frm.doc.company,
					customer: me.frm.doc.customer,
					bill_to: me.frm.doc.bill_to,
				}
			},
			callback: function (r) {
				if (r.message && !r.exc) {
					frappe.run_serially([
						() => me.frm.set_value(r.message),
						() => me.setup_contact_no_fields(r.message.contact_nos),
					]);
				}
			}
		});
	}

	bill_to() {
		this.get_bill_to_details();
	}

	get_bill_to_details() {
		let me = this;

		return frappe.call({
			method: "erpnext.projects.doctype.project.project.get_bill_to_details",
			args: {
				args: {
					doctype: me.frm.doc.doctype,
					company: me.frm.doc.company,
					customer: me.frm.doc.customer,
					bill_to: me.frm.doc.bill_to,
				}
			},
			callback: function (r) {
				if (r.message && !r.exc) {
					frappe.run_serially([
						() => me.frm.set_value(r.message),
					]);
				}
			}
		});
	}

	billing_contact_person() {
		erpnext.utils.get_contact_details(this.frm, "billing_");
	}

	customer_address() {
		erpnext.utils.get_address_display(this.frm, "customer_address", "address_display");
	}

	billing_address() {
		erpnext.utils.get_address_display(this.frm, "billing_address", "billing_address_display");
	}

	service_template(doc, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		this.get_service_template_details(row);
	}

	get_service_template_details(row) {
		if (row && row.service_template) {
			return frappe.call({
				method: "erpnext.projects.doctype.service_template.service_template.get_service_template_details",
				args: {
					service_template: row.service_template
				},
				callback: (r) => {
					if (r.message) {
						frappe.model.set_value(row.doctype, row.name, r.message);
					}
				}
			});
		}
	}

	before_service_templates_remove(doc, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		let cant_change = row.has_sales_order || row.has_service_warranty;

		if (cant_change) {
			frappe.throw(
				__(
					"Cannot remove Service Template <b>{0}</b>: {1} because it has transactions against it",
					[row.service_template, row.service_template_name]
				)
			);
		}
	}

	set_items_and_totals_html() {
		this.frm.get_field("service_items_html").$wrapper.html(this.frm.doc.__onload && this.frm.doc.__onload.service_items_html || '');
		this.frm.get_field("material_items_html").$wrapper.html(this.frm.doc.__onload && this.frm.doc.__onload.material_items_html || '');
		this.frm.get_field("consumable_items_html").$wrapper.html(this.frm.doc.__onload && this.frm.doc.__onload.consumable_items_html || '');
		this.frm.get_field("sales_summary_html").$wrapper.html(this.frm.doc.__onload && this.frm.doc.__onload.sales_summary_html || '');
	}

	project_type() {
		this.get_project_type_defaults();
	}

	get_project_type_defaults() {
		let me = this;

		if (me.frm.doc.project_type) {
			return frappe.call({
				method: "erpnext.projects.doctype.project_type.project_type.get_project_type_defaults",
				args: {
					project_type: me.frm.doc.project_type
				},
				callback: function (r) {
					if (!r.exc) {
						return me.frm.set_value(r.message);
					}
				}
			});
		}
	}

	set_service_advisor_from_user() {
		if (!this.frm.get_field('service_advisor') || this.frm.doc.service_advisor || !this.frm.doc.__islocal) {
			return;
		}

		crm.utils.get_sales_person_from_user(sales_person => {
			if (sales_person) {
				this.frm.set_value('service_advisor', sales_person);
			}
		});
	}

	select_appointment() {
		let me = this;
		let dialog = new frappe.ui.Dialog({
			title: __("Select Appointment"),
			fields: [
				{
					label: __("Appointment Date"),
					fieldname: "scheduled_date",
					fieldtype: "Date",
					default: me.frm.doc.project_date || frappe.datetime.get_today(),
				},
				{
					label: __("Appointment"),
					fieldname: "appointment",
					fieldtype: "Link",
					options: "Appointment",
					only_select: 1,
					get_query: () => {
						let filters = {
							'docstatus': 1,
							'status': ['!=', 'Rescheduled']
						};
						if (dialog.get_value('scheduled_date')) {
							filters['scheduled_date'] = dialog.get_value('scheduled_date');
						}
						if (dialog.get_value('customer')) {
							filters['appointment_for'] = "Customer";
							filters['party_name'] = dialog.get_value('customer');
						}
						return {
							filters: filters
						}
					},
				},
			]
		});

		dialog.set_primary_action(__("Select"), function () {
			let appointment = dialog.get_value('appointment');
			me.get_appointment_details(appointment).then(() => {
				dialog.hide();
			})
		});

		dialog.show();
	}

	get_appointment_details(appointment) {
		let me = this;

		if (appointment) {
			return frappe.call({
				method: "erpnext.overrides.appointment.appointment_hooks.get_project",
				args: {
					source_name: appointment,
					target_doc: me.frm.doc,
				},
				callback: function (r) {
					if (!r.exc) {
						frappe.model.sync(r.message);
						me.frm.dirty();
						me.get_all_contact_nos();
						me.frm.refresh_fields();
					}
				}
			});
		} else {
			me.frm.dirty();
			return me.frm.set_value({
				"appointment": null,
				"appointment_dt": null,
			});
		}
	}

	percent_complete() {
		this.set_percent_complete_read_only();
	}

	set_percent_complete_read_only() {
		let read_only = cint(this.frm.doc.percent_complete_method != "Manual");
		this.frm.set_df_property("percent_complete", "read_only", read_only);
	}

	set_status_read_only() {
		let read_only = this.frm.doc.project_status ? 1 : 0;
		this.frm.set_df_property("status", "read_only", read_only);
	}

	open_form(doctype) {
		let me = this;

		let item_table_fieldnames = {
			'Maintenance Visit': 'purposes',
			'Stock Entry': 'items',
			'Delivery Note': 'items',
			'Timesheet': 'time_logs',
		};

		let items_fieldname = item_table_fieldnames[doctype];

		frappe.new_doc(doctype, {
			customer: me.frm.doc.customer,
			party: me.frm.doc.customer,
			party_name: me.frm.doc.customer,
			quotation_to: 'Customer',
			party_type: 'Customer',
			project: me.frm.doc.name,
			item_code: me.frm.doc.item_code,
			serial_no: me.frm.doc.serial_no,
			item_serial_no: me.frm.doc.serial_no
		}).then(r => {
			if (items_fieldname) {
				cur_frm.doc[items_fieldname] = [];
				let child = cur_frm.add_child(items_fieldname, {
					project: me.frm.doc.name
				});
				cur_frm.refresh_field(items_fieldname);
			}
		});
	}

	make_sales_invoice() {
		let me = this;
		me.frm.check_if_unsaved();

		if (
			me.frm.doc.default_depreciation_percentage
			|| me.frm.doc.default_underinsurance_percentage
			|| me.frm.doc.insurance_excess_amount
			|| me.frm.doc.insurance_excess_percentage
			|| (me.frm.doc.non_standard_depreciation || []).length
			|| (me.frm.doc.non_standard_underinsurance || []).length
		) {
			let html = `
<div class="text-center">
	<button type="button" class="btn btn-primary btn-bill-customer">${__("Bill Excess/Depreciation to <b>Customer (User)</b>")}</button>
	<br/><br/>
	<button type="button" class="btn btn-primary btn-bill-insurance">${__("Bill to <b>Insurance Company</b>")}</button>
</div>
`;

			let dialog = new frappe.ui.Dialog({
				title: __("Depreciation Invoice"),
				fields: [
					{fieldtype: "HTML", options: html}
				],
			});

			dialog.show();

			$('.btn-bill-customer', dialog.$wrapper).click(function () {
				dialog.hide();
				me._make_sales_invoice('Depreciation Amount Only');
			});
			$('.btn-bill-insurance', dialog.$wrapper).click(function () {
				dialog.hide();
				me._make_sales_invoice('After Depreciation Amount');
			});
		} else {
			me._make_sales_invoice();
		}
	}

	_make_sales_invoice(depreciation_type) {
		return frappe.call({
			method: "erpnext.projects.doctype.project.project.make_sales_invoice",
			args: {
				"project_name": this.frm.doc.name,
				"depreciation_type": depreciation_type,
			},
			callback: function (r) {
				if (!r.exc) {
					let doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	}

	make_delivery_note() {
		let me = this;
		me.frm.check_if_unsaved();
		return frappe.call({
			method: "erpnext.projects.doctype.project.project.make_delivery_note",
			args: {
				"project_name": me.frm.doc.name,
			},
			callback: function (r) {
				if (!r.exc) {
					let doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	}

	make_sales_order(items_type) {
		let me = this;
		me.frm.check_if_unsaved();
		return frappe.call({
			method: "erpnext.projects.doctype.project.project.make_sales_order",
			args: {
				"project_name": me.frm.doc.name,
				"items_type": items_type,
			},
			callback: function (r) {
				if (!r.exc) {
					let doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	}

	make_quotation() {
		this.frm.check_if_unsaved();
		return frappe.call({
			method: "erpnext.projects.doctype.project.project.make_quotation",
			args: {
				"project_name": this.frm.doc.name,
			},
			callback: function (r) {
				if (!r.exc) {
					let doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	}

	make_material_request() {
		this.frm.check_if_unsaved();
		return frappe.call({
			method: "erpnext.projects.doctype.project.project.make_material_request",
			args: {
				"project_name": this.frm.doc.name,
			},
			callback: (r) => {
				if (!r.exc) {
					let doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	}

	make_stock_entry(purpose) {
		this.frm.check_if_unsaved();
		return frappe.call({
			method: "erpnext.projects.doctype.project.project.make_stock_entry",
			args: {
				"project_name": this.frm.doc.name,
				"purpose": purpose,
			},
			callback: (r) => {
				if (!r.exc) {
					let doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	}

	create_service_warranties() {
		this.frm.check_if_unsaved();
		frappe.confirm(__("Please confirm creation of Service Warranty"), () => this._create_service_warranties());
	}

	_create_service_warranties() {
		return frappe.call({
			method: "erpnext.projects.doctype.project.project.create_service_warranties",
			args: {
				"project_name": this.frm.doc.name,
			},
			callback: (r) => {
				if (r.message) {
					this.frm.reload_doc();
				}
			}
		});
	}
};

extend_cscript(cur_frm.cscript, new erpnext.projects.ProjectController({frm: cur_frm}));
