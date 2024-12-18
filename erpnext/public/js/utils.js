// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt
frappe.provide("erpnext");
frappe.provide("erpnext.utils");

$.extend(erpnext, {
	get_currency: function(company) {
		if(!company && cur_frm)
			company = cur_frm.doc.company;
		if(company)
			return frappe.get_doc(":Company", company).default_currency || frappe.boot.sysdefaults.currency;
		else
			return frappe.boot.sysdefaults.currency;
	},

	get_presentation_currency_list: () => {
		const docs = frappe.boot.docs;
		let currency_list = docs.filter(d => d.doctype === ":Currency").map(d => d.name);
		currency_list.unshift("");
		return currency_list;
	},

	toggle_naming_series: function() {
		if(cur_frm.fields_dict.naming_series && !cur_frm.doc.__islocal) {
			cur_frm.toggle_display("naming_series", false);
		}
	},

	hide_company: function(frm) {
		if (!frm) {
			frm = cur_frm;
		}

		if (frm.fields_dict.company) {
			let companies = Object.keys(locals[":Company"] || {});
			let company_user_permissions = frappe.defaults.get_user_permissions()['Company'];

			if (companies.length === 1) {
				if (!frm.doc.company) {
					frm.set_value("company", companies[0]);
				}
				frm.toggle_display("company", false);
			} else if (company_user_permissions && company_user_permissions.length === 1) {
				if (!frm.doc.company) {
					frm.set_value("company", company_user_permissions[0].doc);
				}
				frm.toggle_display("company", false);
			} else if (erpnext.last_selected_company) {
				if( !frm.doc.company) {
					frm.set_value("company", erpnext.last_selected_company);
				}
			}
		}

		if (frm.fields_dict.branch) {
			let branch_user_permissions = frappe.defaults.get_user_permissions()['Branch'];
			if (branch_user_permissions && branch_user_permissions.length === 1) {
				if (!frm.doc.branch) {
					frm.set_value("branch", branch_user_permissions[0].doc);
				}
				frm.toggle_display("branch", false);
			}
		}
	},

	is_perpetual_inventory_enabled: function(company) {
		if(company) {
			return frappe.get_doc(":Company", company).enable_perpetual_inventory
		}
	},

	stale_rate_allowed: () => {
		return cint(frappe.boot.sysdefaults.allow_stale);
	},

	setup_serial_no: function(on_click) {
		let grid_row = cur_frm.open_grid_row();
		if(!grid_row
			|| !grid_row.grid_form.fields_dict.serial_no
			|| grid_row.grid_form.fields_dict.serial_no.get_status() !== "Write"
		) {
			return;
		}

		let $btn = $('<button class="btn btn-sm btn-default">'+__("Add Serial No")+'</button>').appendTo(
			$("<div>").css({"margin-bottom": "5px", "margin-top": "5px"})
				.appendTo(grid_row.grid_form.fields_dict.serial_no.$wrapper)
		);

		$btn.on("click", function() {
			if (on_click) {
				on_click();
			} else {
				new erpnext.stock.SerialBatchSelector(grid_row.frm, grid_row.doc);
			}
		});
	},

	route_to_adjustment_jv: (args) => {
		frappe.model.with_doctype('Journal Entry', () => {
			// route to adjustment Journal Entry to handle Account Balance and Stock Value mismatch
			let journal_entry = frappe.model.get_new_doc('Journal Entry');

			args.accounts.forEach((je_account) => {
				let child_row = frappe.model.add_child(journal_entry, "accounts");
				child_row.account = je_account.account;
				child_row.debit_in_account_currency = je_account.debit_in_account_currency;
				child_row.credit_in_account_currency = je_account.credit_in_account_currency;
				child_row.party_type = "" ;
			});
			frappe.set_route('Form','Journal Entry', journal_entry.name);
		});
	},
});


$.extend(erpnext.utils, {
	set_party_dashboard_indicators: function(frm) {
		if(frm.doc.__onload && frm.doc.__onload.dashboard_info) {
			var company_wise_info = frm.doc.__onload.dashboard_info;
			if(company_wise_info.length > 1) {
				company_wise_info.forEach(function(info) {
					erpnext.utils.add_indicator_for_multicompany(frm, info);
				});
			} else if (company_wise_info.length === 1) {
				if ('billing_this_year' in company_wise_info[0]) {
					frm.dashboard.add_indicator(__('Annual Billing: {0}',
						[format_currency(company_wise_info[0].billing_this_year, company_wise_info[0].currency)]), 'blue');
				}

				if ('total_unpaid' in company_wise_info[0]) {
					frm.dashboard.add_indicator(__('Total Unpaid: {0}',
						[format_currency(company_wise_info[0].total_unpaid, company_wise_info[0].currency)]),
						company_wise_info[0].total_unpaid ? 'orange' : 'green');
				}

				if(company_wise_info[0].loyalty_points) {
					frm.dashboard.add_indicator(__('Loyalty Points: {0}',
						[company_wise_info[0].loyalty_points]), 'blue');
				}
			}
		}
	},

	add_indicator_for_multicompany: function(frm, info) {
		frm.dashboard.stats_area.removeClass('hidden');
		frm.dashboard.stats_area_row.addClass('flex');
		frm.dashboard.stats_area_row.css('flex-wrap', 'wrap');

		var color = info.total_unpaid ? 'orange' : 'green';

		var indicator = $('<div class="flex-column col-xs-6">'+
			'<div style="margin-top:10px"><h6>'+info.company+'</h6></div>'+

			'<div class="badge-link small" style="margin-bottom:10px"><span class="indicator blue">'+
			'Annual Billing: '+format_currency(info.billing_this_year, info.currency)+'</span></div>'+

			'<div class="badge-link small" style="margin-bottom:10px">'+
			'<span class="indicator '+color+'">Total Unpaid: '
			+format_currency(info.total_unpaid, info.currency)+'</span></div>'+


			'</div>').appendTo(frm.dashboard.stats_area_row);

		if(info.loyalty_points){
			$('<div class="badge-link small" style="margin-bottom:10px"><span class="indicator blue">'+
			'Loyalty Points: '+info.loyalty_points+'</span></div>').appendTo(indicator);
		}

		return indicator;
	},

	copy_value_in_all_rows: function(doc, dt, dn, table_fieldname, fieldname) {
		var d = locals[dt][dn];
		if(d[fieldname]){
			var cl = doc[table_fieldname] || [];
			for(var i = 0; i < cl.length; i++) {
				if(!cl[i][fieldname]) cl[i][fieldname] = d[fieldname];
			}
		}
		refresh_field(table_fieldname);
	},

	autofill_warehouse (child_table, warehouse_field, warehouse, force) {
		if ((warehouse || force) && child_table && child_table.length) {
			let doctype = child_table[0].doctype;
			$.each(child_table || [], function(i, item) {
				if (force || !item.force_default_warehouse) {
					frappe.model.set_value(doctype, item.name, warehouse_field, warehouse);
				}
			});
		}
	},

	get_terms: function(tc_name, doc, callback) {
		if(tc_name) {
			return frappe.call({
				method: 'erpnext.setup.doctype.terms_and_conditions.terms_and_conditions.get_terms_and_conditions',
				args: {
					template_name: tc_name,
					doc: doc
				},
				callback: function(r) {
					callback(r)
				}
			});
		}
	},

	make_bank_account: function(doctype, docname) {
		frappe.call({
			method: "erpnext.accounts.doctype.bank_account.bank_account.make_bank_account",
			args: {
				doctype: doctype,
				docname: docname
			},
			freeze: true,
			callback: function(r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		})
	},

	add_dimensions: function(report_name, index) {
		let filters = frappe.query_reports[report_name].filters;

		erpnext.dimension_filters.forEach((dimension) => {
			let found = filters.some(el => el.fieldname === dimension['fieldname']);

			if (!found) {
				filters.splice(index, 0 ,{
					"fieldname": dimension["fieldname"],
					"label": __(dimension["label"]),
					"fieldtype": "Link",
					"options": dimension["document_type"]
				});
			}
		});
	},

	add_additional_gl_filters: function(report_name) {
		let filters = frappe.query_reports[report_name].filters;

		(frappe.boot.additional_gl_filters || []).forEach((additional_filter) => {
			let found = filters.some(el => el.fieldname === additional_filter['fieldname']);

			if (!found) {
				filters.push(additional_filter);
			}
		});
	},

	add_additional_sle_filters: function(report_name) {
		let filters = frappe.query_reports[report_name].filters;

		(frappe.boot.additional_sle_filters || []).forEach((additional_filter) => {
			let found = filters.some(el => el.fieldname === additional_filter['fieldname']);

			if (!found) {
				filters.push(additional_filter);
			}
		});
	},

	make_subscription: function(doctype, docname) {
		frappe.call({
			method: "frappe.automation.doctype.auto_repeat.auto_repeat.make_auto_repeat",
			args: {
				doctype: doctype,
				docname: docname
			},
			callback: function(r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		})
	},

	make_pricing_rule: function(doctype, docname) {
		frappe.call({
			method: "erpnext.accounts.doctype.pricing_rule.pricing_rule.make_pricing_rule",
			args: {
				doctype: doctype,
				docname: docname
			},
			callback: function(r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		})
	},

	/**
	* Checks if the first row of a given child table is empty
	* @param child_table - Child table Doctype
	* @return {Boolean}
	**/
	first_row_is_empty: function(child_table){
		if($.isArray(child_table) && child_table.length > 0) {
			return !child_table[0].item_code;
		}
		return false;
	},

	/**
	* Removes the first row of a child table if it is empty
	* @param {_Frm} frm - The current form
	* @param {String} child_table_name - The child table field name
	* @return {Boolean}
	**/
	remove_empty_first_row: function(frm, child_table_name){
		const rows = frm['doc'][child_table_name];
		if (this.first_row_is_empty(rows)){
			frm['doc'][child_table_name] = rows.splice(1);
		}
		return rows;
	},
	get_tree_options: function(option) {
		// get valid options for tree based on user permission & locals dict
		let unscrub_option = frappe.model.unscrub(option);
		let user_permission = frappe.defaults.get_user_permissions();
		let options;

		if(user_permission && user_permission[unscrub_option]) {
			options = user_permission[unscrub_option].map(perm => perm.doc);
		} else {
			options = $.map(locals[`:${unscrub_option}`], function(c) { return c.name; }).sort();
		}

		// filter unique values, as there may be multiple user permissions for any value
		return options.filter((value, index, self) => self.indexOf(value) === index);
	},
	get_tree_default: function(option) {
		// set default for a field based on user permission
		let options = this.get_tree_options(option);
		if(options.includes(frappe.defaults.get_default(option))) {
			return frappe.defaults.get_default(option);
		} else {
			return options[0];
		}
	},
	copy_parent_value_in_all_row: function(doc, dt, dn, table_fieldname, fieldname, parent_fieldname) {
		var d = locals[dt][dn];
		if(d[fieldname]){
			var cl = doc[table_fieldname] || [];
			for(var i = 0; i < cl.length; i++) {
				cl[i][fieldname] = doc[parent_fieldname];
			}
		}
		refresh_field(table_fieldname);
	},

	set_item_naming_series_options: function(frm) {
		frappe.model.with_doctype("Item", function() {
			var item_series = cstr(frappe.meta.get_docfield("Item", "naming_series").options).split("\n");
			if (item_series.length || item_series[0]) {
				item_series.unshift('');
			}
			frm.set_df_property("item_naming_series", "options", item_series.join('\n'));
		});
	},

	add_payment_reconciliation_button: function(party_type, page, get_values) {
		page.add_inner_button(__("Payment Reconciliation"), function() {
			var values = get_values();
			frappe.new_doc("Payment Reconciliation").then(() => {
				cur_frm.set_value({
					company: values.company,
					party_type: party_type,
					party: values[frappe.scrub(party_type)],
					receivable_payable_accocunt: values.account
				});
			});
		});
	},

	set_item_overrides: function(frm) {
		frappe.call({
			method: "erpnext.stock.doctype.item.item.get_item_override_values",
			args: {
				args: {
					brand: frm.doc.brand,
					item_group: frm.doc.item_group,
					item_source: frm.doc.item_source,
					variant_of: frm.doc.variant_of
				}
			},
			callback: function (r) {
				if (r.message) {
					if (frm.doc.__islocal) {
						$.each(r.message.fieldnames || [], function (i, fieldname) {
							if (frm.fields_dict[fieldname]) {
								let default_read_only = frappe.meta.get_docfield("Item", fieldname);
								default_read_only = default_read_only ? default_read_only.read_only : 0;
								frm.set_df_property(fieldname, 'read_only', r.message.values.hasOwnProperty(fieldname) ? 1 : default_read_only);
							}
						});
					}

					frappe.run_serially([
						() => {
							if (frm.set_values) {
								frm.set_values(r.message.values || {});
							} else {
								frm.set_value(r.message.values || {});
							}
						},
						() => frm.layout?.refresh_section_collapse?.(),
					]);
				}
			}
		});
	},

	set_customer_overrides: function(frm) {
		frappe.call({
			method: "erpnext.selling.doctype.customer.customer.get_customer_override_values",
			args: {
				args: {
					customer_group: frm.doc.customer_group,
				}
			},
			callback: function (r) {
				if (r.message) {
					if (frm.doc.__islocal) {
						$.each(r.message.fieldnames || [], function (i, fieldname) {
							if (frm.fields_dict[fieldname]) {
								let default_read_only = frappe.meta.get_docfield("Customer", fieldname);
								default_read_only = default_read_only ? default_read_only.read_only : 0;
								frm.set_df_property(fieldname, 'read_only', r.message.values.hasOwnProperty(fieldname) ? 1 : default_read_only);
							}
						});
					}

					frappe.run_serially([
						() => {
							if (frm.set_values) {
								frm.set_values(r.message.values || {});
							} else {
								frm.set_value(r.message.values || {});
							}
						},
						() => frm.layout?.refresh_section_collapse?.(),
					]);
				}
			}
		});
	},

	create_new_doc: function (doctype, update_fields) {
		frappe.model.with_doctype(doctype, function() {
			var new_doc = frappe.model.get_new_doc(doctype);
			for (let [key, value] of Object.entries(update_fields)) {
				new_doc[key] = value;
			}
			frappe.ui.form.make_quick_entry(doctype, null, null, new_doc);
		});
	},

	setup_remove_zero_qty_rows(frm, qty_fields) {
		if (!qty_fields || !qty_fields.length) {
			qty_fields = 'qty';
		}
		if (!Array.isArray(qty_fields)) {
			qty_fields = [qty_fields];
		}

		if (frm.doc.docstatus === 0) {
			frm.fields_dict.items.grid.add_custom_button(__("Remove 0 Qty Rows"), function () {
				let actions = [];
				for (let d of frm.doc.items || []) {
					let qtys = qty_fields.map(f => flt(d[f], precision('qty', d)));
					if (!qtys.some(Boolean)) {
						actions.push(() => frm.fields_dict.items.grid.get_row(d.name).remove());
					}
				}

				return frappe.run_serially(actions);
			});
		}
	},

	show_progress_for_qty(args) {
		let bars = [];
		let added_min = 0;

		let description = args.description || [];
		if (typeof description == "string") {
			description = [description];
		}

		let total_qty = flt(args.total_qty) || 0;
		if (!total_qty) {
			return "";
		}

		for (let d of args.progress_bars) {
			let title = d.title || "";

			let completed_qty = flt(d.completed_qty) || 0;
			if (completed_qty <= 0 && !d.add_min_width) {
				continue;
			}

			if (!d.description_only) {
				let bar_width = flt(completed_qty / total_qty * 100, 2);
				bar_width -= added_min;
				added_min = 0;

				bar_width = Math.max(bar_width, 0)
				if (bar_width == 0 && d.add_min_width) {
					added_min += flt(d.add_min_width);
					bar_width += flt(d.add_min_width);
				}

				bars.push({
					"title": strip_html(title),
					"bar_width": bar_width,
					"width": bar_width + "%",
					"progress_class": d.progress_class || "progress-bar-success",
				});
			}

			if (title) {
				description.push(title);
			}
		}

		if (args.frm) {
			args.frm.dashboard.add_progress(args.title || "Progress", bars, description.join("<br>"));
		} else if (args.as_html) {
			let html_progress_bars = [];
			for (let d of bars) {
				html_progress_bars.push(`
					<div class="progress-bar ${d.progress_class}" role="progressbar"
						aria-valuenow="${d.bar_width}" aria-valuemin="0" aria-valuemax="100"
						title="${d.title}"
						style="width: ${d.width};">
					</div>
				`);
			}
			return `
				<div class="progress">
					${html_progress_bars.join("")}
				</div>
			`;
		}
	},

	format_vehicle_id: function (frm, fieldname) {
		let value = frm.doc[fieldname];
		if (value) {
			value = erpnext.utils.get_formatted_vehicle_id(value);
			frm.doc[fieldname] = value;
			frm.refresh_field(fieldname);
		}
	},

	get_formatted_vehicle_id(value) {
		return cstr(value).replace(/\s+/g, "").toUpperCase();
	},

	get_vehicle_make_model: function (item_code, callback) {
		return frappe.call({
			method: "erpnext.vehicles.doctype.vehicle.vehicle.get_vehicle_make_model",
			args: {
				item_code: item_code,
			},
			callback: (r) => {
				callback?.(r);
			},
		});
	},

	validate_duplicate_vehicle: function (doc, fieldname) {
		let value = doc[fieldname];
		if (value) {
			return frappe.call({
				method: "erpnext.vehicles.doctype.vehicle.vehicle.validate_duplicate_vehicle",
				args: {
					fieldname: fieldname,
					value: value,
					exclude: doc.__islocal ? null : doc.name
				}
			});
		}
	},

	get_license_plate_with_prefix(plate_region, license_plate, callback) {
		if (plate_region) {
			return frappe.call({
				method: "erpnext.vehicles.doctype.vehicle_plate_region.vehicle_plate_region.get_license_plate_with_prefix",
				args: {
					vehicle_plate_region: plate_region,
					license_plate: license_plate || "",
				},
				callback: (r) => {
					callback?.(r.message);
				},
			});
		}
	}
});

erpnext.utils.select_alternate_items = function(opts) {
	const frm = opts.frm;
	const warehouse_field = opts.warehouse_field || 'warehouse';
	const item_code_field = opts.item_field || opts.item_code_field || 'item_code';
	const item_name_field = opts.item_name_field || 'item_name';
	let qty_field = opts.qty_field || (frm.doctype === 'Work Order' ? 'required_qty' : 'qty');

	this.data = [];
	const dialog = new frappe.ui.Dialog({
		title: __("Select Alternate Item"),
		size: "extra-large",
		fields: [
			{
				label: __("Items"),
				fieldname: "alternative_items",
				fieldtype: "Table",
				cannot_add_rows: true,
				in_place_edit: true,
				data: this.data,
				get_data: () => {
					return this.data;
				},
				fields: [
					{
						fieldtype: "Link",
						fieldname: "item_code",
						options: "Item",
						in_list_view: 1,
						columns: 2,
						read_only: 1,
						bold: 1,
						label: __("Original Item")
					},
					{
						fieldtype: "Data",
						fieldname: "item_name",
						in_list_view: 1,
						columns: 2,
						read_only: 1,
						label: __("Original Item Name"),
					},
					{
						fieldtype: "Link",
						fieldname: "alternate_item",
						options: "Item",
						in_list_view: 1,
						label: __("Alternate Item"),
						onchange: function() {
							const alternate_item = this.grid_row.doc.alternate_item;
							const stock_item = alternate_item || this.grid_row.doc.item_code;
							const warehouse = this.grid_row.doc.warehouse;
							if (stock_item && warehouse) {
								frappe.call({
									method: "erpnext.stock.utils.get_latest_stock_qty",
									args: {
										item_code: stock_item,
										warehouse: warehouse
									},
									callback: (r) => {
										this.grid_row.doc.actual_qty = r.message || 0;
										dialog.refresh();
									}
								});
							}

							if (alternate_item) {
								frappe.db.get_value("Item", alternate_item, 'item_name', (r) => {
									if (r) {
										this.grid_row.doc.alternate_item_name = r.item_name || "";
									} else {
										this.grid_row.doc.alternate_item_name = "";
									}
									dialog.refresh();
								});
							} else {
								this.grid_row.doc.alternate_item_name = "";
								dialog.refresh();
							}
						},
						get_query: (e) => {
							return {
								query: "erpnext.stock.doctype.item_alternative.item_alternative.alternative_item_query",
								filters: {
									item_code: e.item_code
								}
							};
						}
					},
					{
						fieldtype: "Data",
						fieldname: "alternate_item_name",
						in_list_view: 1,
						columns: 2,
						read_only: 1,
						label: __("Alternate Item Name"),
					},
					{
						fieldtype: "Link",
						fieldname: "warehouse",
						options: 'Warehouse',
						read_only: 1,
						label: __('Warehouse'),
					},
					{
						fieldtype: "Float",
						fieldname: "actual_qty",
						default: 0,
						read_only: 1,
						in_list_view: 1,
						columns: 2,
						label: __('In Stock'),
					},
					{
						fieldtype: "Data",
						fieldname: "docname",
						hidden: 1
					},
				]
			},
		],
		primary_action: function() {
			const args = this.get_values()["alternative_items"];
			const alternative_items = args.filter(d => d.alternate_item && d.item_code != d.alternate_item);

			for (const d of alternative_items) {
				let row = frappe.get_doc(opts.child_doctype, d.docname);
				let qty = row[qty_field];
				row[item_code_field] = d.alternate_item;

				frappe.model.set_value(row.doctype, row.name, item_name_field, d.alternate_item_name);
				frm.script_manager.trigger(item_code_field, row.doctype, row.name).then(() => {
					if (qty != null) {
						frappe.model.set_value(row.doctype, row.name, qty_field, flt(qty));
					}

					if (opts.original_item_field) {
						frappe.model.set_value(row.doctype, row.name, opts.original_item_field, d.item_code);
					}
				});
			}

			frm.refresh_field(opts.child_docname);
			dialog.hide();
		},
		primary_action_label: __('Update')
	});

	for (const d of frm.doc[opts.child_docname] || []) {
		if (!opts.condition || opts.condition(d)) {
			dialog.fields_dict.alternative_items.df.data.push({
				"docname": d.name,
				"item_code": d[item_code_field],
				"item_name": d[item_name_field],
				"warehouse": d[warehouse_field],
				"actual_qty": d.actual_qty,
				"disable_item_formatter": 1,
			});
		}
	}

	this.data = dialog.fields_dict.alternative_items.df.data;
	dialog.fields_dict.alternative_items.grid.refresh();
	dialog.show();
}

erpnext.utils.update_child_items = function(opts) {
	const frm = opts.frm;
	const cannot_add_row = (typeof opts.cannot_add_row === 'undefined') ? true : opts.cannot_add_row;
	const child_docname = (typeof opts.cannot_add_row === 'undefined') ? "items" : opts.child_docname;
	this.data = [];
	const fields = [{
		fieldtype:'Data',
		fieldname:"docname",
		read_only: 1,
		hidden: 1,
	}, {
		fieldtype:'Link',
		fieldname:"item_code",
		options: 'Item',
		in_list_view: 1,
		read_only: 0,
		disabled: 0,
		label: __('Item Code')
	}, {
		fieldtype:'Float',
		fieldname:"qty",
		default: 0,
		read_only: 0,
		in_list_view: 1,
		label: __('Qty')
	}, {
		fieldtype:'Currency',
		fieldname:"rate",
		default: 0,
		read_only: 0,
		in_list_view: 1,
		label: __('Rate')
	}];

	if (frm.doc.doctype == 'Sales Order' || frm.doc.doctype == 'Purchase Order' ) {
		fields.splice(2, 0, {
			fieldtype: 'Date',
			fieldname: frm.doc.doctype == 'Sales Order' ? "delivery_date" : "schedule_date",
			in_list_view: 1,
			label: frm.doc.doctype == 'Sales Order' ? __("Delivery Date") : __("Reqd by date"),
			reqd: 1
		})
		fields.splice(3, 0, {
			fieldtype: 'Float',
			fieldname: "conversion_factor",
			in_list_view: 1,
			label: __("Conversion Factor")
		})
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Update Items"),
		size: "extra-large",
		fields: [
			{
				fieldname: "trans_items",
				fieldtype: "Table",
				label: "Items",
				cannot_add_rows: cannot_add_row,
				in_place_edit: true,
				reqd: 1,
				data: this.data,
				get_data: () => {
					return this.data;
				},
				fields: fields
			},
		],
		primary_action: function() {
			const trans_items = this.get_values()["trans_items"];
			frappe.call({
				method: 'erpnext.controllers.accounts_controller.update_child_qty_rate',
				freeze: true,
				args: {
					'parent_doctype': frm.doc.doctype,
					'trans_items': trans_items,
					'parent_doctype_name': frm.doc.name,
					'child_docname': child_docname
				},
				callback: function() {
					frm.reload_doc();
				}
			});
			this.hide();
			refresh_field("items");
		},
		primary_action_label: __('Update')
	});

	frm.doc[opts.child_docname].forEach(d => {
		dialog.fields_dict.trans_items.df.data.push({
			"docname": d.name,
			"name": d.name,
			"item_code": d.item_code,
			"delivery_date": d.delivery_date,
			"schedule_date": d.schedule_date,
			"conversion_factor": d.conversion_factor,
			"qty": d.qty,
			"rate": d.rate,
		});
		this.data = dialog.fields_dict.trans_items.df.data;
		dialog.fields_dict.trans_items.grid.refresh();
	})
	dialog.show();
}

erpnext.utils.map_current_doc = function(opts) {
	if(opts.get_query_filters) {
		opts.get_query = function() {
			return {filters: opts.get_query_filters};
		}
	}
	var _map = function() {
		if($.isArray(cur_frm.doc.items) && cur_frm.doc.items.length > 0) {
			// remove first item row if empty
			if(!cur_frm.doc.items[0].item_code) {
				cur_frm.doc.items = cur_frm.doc.items.splice(1);
			}

			// find the doctype of the items table
			var items_doctype = frappe.meta.get_docfield(cur_frm.doctype, 'items').options;

			// find the link fieldname from items table for the given
			// source_doctype
			var link_fieldname = null;
			frappe.get_meta(items_doctype).fields.forEach(function(d) {
				if(d.options===opts.source_doctype) link_fieldname = d.fieldname; });

			// search in existing items if the source_name is already set and full qty fetched
			var already_set = false;
			var item_qty_map = {};

			$.each(cur_frm.doc.items, function(i, d) {
				opts.source_name.forEach(function(src) {
					if(d[link_fieldname]==src) {
						already_set = true;
						if (item_qty_map[d.item_code])
							item_qty_map[d.item_code] += flt(d.qty);
						else
							item_qty_map[d.item_code] = flt(d.qty);
					}
				});
			});

			if(already_set) {
				opts.source_name.forEach(function(src) {
					frappe.model.with_doc(opts.source_doctype, src, function(r) {
						var source_doc = frappe.model.get_doc(opts.source_doctype, src);
						$.each(source_doc.items || [], function(i, row) {
							if(row.qty > flt(item_qty_map[row.item_code])) {
								already_set = false;
								return false;
							}
						})
					})

					if(already_set) {
						frappe.msgprint(__("You have already selected items from {0} {1}",
							[opts.source_doctype, src]));
						return;
					}

				})
			}
		}

		return frappe.call({
			// Sometimes we hit the limit for URL length of a GET request
			// as we send the full target_doc. Hence this is a POST request.
			type: "POST",
			method: 'frappe.model.mapper.map_docs',
			args: {
				"method": opts.method,
				"source_names": opts.source_name,
				"target_doc": cur_frm.doc,
				'args': opts.args
			},
			callback: function(r) {
				if(!r.exc) {
					var doc = frappe.model.sync(r.message);
					cur_frm.dirty();
					cur_frm.refresh_fields();
				}
			}
		});
	}
	if(opts.source_doctype) {
		var d = new frappe.ui.form.MultiSelectDialog({
			doctype: opts.source_doctype,
			target: opts.target,
			date_field: opts.date_field || undefined,
			setters: opts.setters,
			columns: opts.columns,
			get_query: opts.get_query,
			action: function(selections, args) {
				let values = selections;
				if(values.length === 0){
					frappe.msgprint(__("Please select {0}", [opts.source_doctype]))
					return;
				}
				opts.source_name = values;
				opts.setters = args;
				d.dialog.hide();
				_map();
			},
		});
	} else if(opts.source_name) {
		opts.source_name = [opts.source_name];
		_map();
	}
}

erpnext.utils.has_valuation_read_permission = function() {
	let allowed_role = frappe.defaults.get_default('restrict_stock_valuation_to_role');
	return !allowed_role || frappe.user.has_role(allowed_role);
}

erpnext.utils.query_report_local_refresh = function() {
	if (frappe.query_report && frappe.query_report.datatable) {
		frappe.query_report.datatable.datamanager.rowCount = 0;
		frappe.query_report.datatable.datamanager.columns = [];
		frappe.query_report.datatable.datamanager.rows = [];

		frappe.query_report.datatable.datamanager.prepareColumns();
		frappe.query_report.datatable.datamanager.prepareRows();
		frappe.query_report.datatable.datamanager.prepareTreeRows();
		frappe.query_report.datatable.datamanager.prepareRowView();
		frappe.query_report.datatable.datamanager.prepareNumericColumns();

		frappe.query_report.datatable.bodyRenderer.render();
	}
}

frappe.form.link_formatters['Item'] = function(value, doc) {
	if (
		doc
		&& doc.item_name
		&& doc.item_name !== value
		&& !doc.disable_item_formatter
	) {
		return value ? value + ': ' + doc.item_name : doc.item_name;
	} else {
		return value;
	}
}

frappe.form.link_formatters['Employee'] = function(value, doc) {
	var employee_name = doc.employee_name || (doc.party_type === "Employee" && doc.party_name);
	if(doc && employee_name && employee_name !== value && !doc.disable_party_name_formatter) {
		return value ? value + ': ' + employee_name: employee_name;
	} else {
		return value;
	}
}

frappe.form.link_formatters['Customer'] = function(value, doc) {
	if(doc && doc.party_type === "Customer" && doc.party_name && doc.party_name !== value && !doc.disable_party_name_formatter) {
		return value ? value + ': ' + doc.party_name: doc.party_name;
	} else {
		return value;
	}
}

frappe.form.global_formatters.push(function (value, df, options, doc) {
	if (df && doc) {
		if (['alt_uom_qty', 'alt_uom_size', 'alt_uom_size_std'].includes(df.fieldname) && doc.alt_uom) {
			return cstr(value) + " " + cstr(doc.alt_uom)
		}

		if (['alt_uom_rate', 'base_alt_uom_rate'].includes(df.fieldname) && doc.alt_uom) {
			return cstr(value) + "/" + cstr(doc.alt_uom);
		}
	}
});

// add description on posting time
$(document).on('app_ready', function() {
	if(!frappe.datetime.is_timezone_same()) {
		$.each(["Stock Reconciliation", "Stock Entry", "Stock Ledger Entry",
			"Delivery Note", "Purchase Receipt", "Sales Invoice"], function(i, d) {
			frappe.ui.form.on(d, "onload", function(frm) {
				cur_frm.set_df_property("posting_time", "description",
					frappe.sys_defaults.time_zone);
			});
		});
	}
});
