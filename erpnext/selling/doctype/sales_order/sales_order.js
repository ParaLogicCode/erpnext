// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

{% include 'erpnext/selling/sales_common.js' %}

frappe.ui.form.on("Sales Order", {
	setup: function(frm) {
		frm.custom_make_buttons = {
			'Delivery Note': __('Delivery Note'),
			'Pick List': __('Pick List'),
			'Sales Invoice': __('Sales Invoice'),
			'Material Request': __('Material Request'),
			'Purchase Order': __('Purchase Order'),
			'Project': __('Project'),
			'Payment Entry': __("Payment"),
			'Purchase Invoice': __('Purchase Invoice'),
			'Work Order': __("Work Order"),
			'Auto Repeat': __("Subscription"),
			'Payment Request': __("Payment Request"),
			'Vehicle': __("Reserved Vehicles"),
			'Packing Slip': __("Packing Slip"),
		}
	},
	refresh: function(frm) {
		if (frm.doc.docstatus === 1
			&& frm.doc.status !== 'Closed'
			&& frm.doc.delivery_status == "To Deliver"
			&& frm.doc.billing_status == "To Bill"
		) {
			frm.add_custom_button(__('Update Items'), () => {
				erpnext.utils.update_child_items({
					frm: frm,
					child_docname: "items",
					child_doctype: "Sales Order Detail",
					cannot_add_row: false,
				})
			});
		}
	},
	onload: function(frm) {
		if (!frm.doc.transaction_date){
			frm.set_value('transaction_date', frappe.datetime.get_today())
		}
		erpnext.queries.setup_queries(frm, "Warehouse", function() {
			return erpnext.queries.warehouse(frm.doc);
		});
		erpnext.queries.setup_warehouse_qty_query(frm);

		if (frm.doc.__islocal) {
			frm.events.delivery_date(frm);
		}
	},

	delivery_date: function(frm) {
		if (frm.doc.delivery_date) {
			$.each(frm.doc.items || [], function (i, d) {
				d.delivery_date = frm.doc.delivery_date;
			});
			refresh_field("items");
		}
	}
});

frappe.ui.form.on("Sales Order Item", {
	item_code: function(frm,cdt,cdn) {
		var row = locals[cdt][cdn];
		if (frm.doc.delivery_date) {
			row.delivery_date = frm.doc.delivery_date;
			refresh_field("delivery_date", cdn, "items");
		} else {
			frm.script_manager.copy_from_first_row("items", row, ["delivery_date"]);
		}
	},
	delivery_date: function(frm, cdt, cdn) {
		if(!frm.doc.delivery_date) {
			erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "items", "delivery_date");
		}
	}
});

erpnext.selling.SalesOrderController = class SalesOrderController extends erpnext.selling.SellingController {
	onload(doc, dt, dn) {
		super.onload();
	}

	refresh(doc, dt, dn) {
		super.refresh();
		this.setup_buttons();
		this.setup_progressbars();

		// formatter for items table
		this.frm.set_indicator_formatter('item_code', function(doc) {
			if (doc.docstatus === 0) {
				if (!doc.is_stock_item || doc.skip_delivery_note) {
					return "blue";
				} else if (!doc.actual_qty) {
					return "red";
				} else if (doc.actual_qty < doc.stock_qty) {
					return "orange";
				} else {
					return "green";
				}
			} else {
				if (!doc.delivered_qty) {
					if (!doc.is_stock_item || doc.skip_delivery_note) {
						return "purple";
					} else {
						return "orange";
					}
				} else if (doc.delivered_qty < doc.qty) {
					return "yellow";
				} else {
					return "green";
				}
			}
		});

		if (this.frm.doc.__islocal) {
			this.set_skip_delivery_note();
		}
	}

	setup_buttons() {
		var me = this;
		let allow_delivery = false;

		if (me.frm.doc.docstatus == 0) {
			me.add_get_latest_price_button();
		}
		if (me.frm.doc.docstatus == 1) {
			me.add_update_price_list_button();
		}

		if (me.frm.doc.docstatus==1) {
			if(me.frm.has_perm("submit")) {
				if(me.frm.doc.status === 'On Hold') {
				   // un-hold
				   me.frm.add_custom_button(__('Resume'), function() {
					   me.update_status('Resume', 'Resumed')
				   }, __("Status"));

				   if (me.frm.doc.delivery_status == "To Deliver" || me.frm.doc.billing_status == "To Bill") {
						// close
						me.frm.add_custom_button(__('Close'), () => me.close_sales_order(), __("Status"))
				   }
				}
				else if(me.frm.doc.status === 'Closed') {
				   // un-close
				   me.frm.add_custom_button(__('Re-Open'), function() {
					   me.update_status('Re-Open', 'Re-Opened')
				   }, __("Status"));
			   }
			}

			if(me.frm.doc.status !== 'Closed') {
				if(me.frm.doc.status !== 'On Hold') {
					allow_delivery = (
						me.frm.doc.items.some(item => !item.skip_delivery_note && item.qty > flt(item.delivered_qty))
						&& !me.frm.doc.skip_delivery_note
					);

					if (me.frm.has_perm("submit")) {
						if(me.frm.doc.delivery_status == "To Deliver" || me.frm.doc.billing_status == "To Bill") {
							// hold
							me.frm.add_custom_button(__('Hold'), () => me.hold_sales_order(), __("Status"))
							// close
							me.frm.add_custom_button(__('Close'), () => me.close_sales_order(), __("Status"))
						}
					}

					// delivery note
					let deliverable_rows = me.frm.doc.items.filter(d => !d.skip_delivery_note);
					let has_undelivered = deliverable_rows.some(d => {
						return flt(d.delivered_qty, precision("qty", d)) < flt(d.qty, precision("qty", d));
					});

					let packable_rows = deliverable_rows.filter(d => d.is_stock_item);
					let has_unpacked = packable_rows.some(d => {
						let produced_qty_order_uom = flt(d.produced_qty) / flt(d.conversion_factor);
						return produced_qty_order_uom
							? flt(d.packed_qty, precision("qty", d)) < flt(produced_qty_order_uom, precision("qty", d))
							: flt(d.packed_qty, precision("qty", d)) < flt(d.qty, precision("qty", d));
					});

					if (
						(me.frm.doc.delivery_status == "To Deliver" || has_undelivered)
						&& ["Sales", "Shopping Cart"].indexOf(me.frm.doc.order_type) !== -1
						&& allow_delivery
					) {
						if (frappe.model.can_create("Delivery Note")) {
							me.frm.add_custom_button(__('Delivery Note'), () => me.make_delivery_note_based_on(), __('Create'));
						}

						if (frappe.model.can_create("Work Order")) {
							me.frm.add_custom_button(__('Work Order'), () => me.make_work_orders(), __('Create'));
						}

						if ((has_unpacked || me.frm.doc.packing_status == "To Pack") && frappe.model.can_create("Packing Slip")) {
							me.frm.add_custom_button(__('Packing Slip'), () => me.make_packing_slip(), __('Create'));
						}

						if (frappe.model.can_create("Pick List")) {
							me.frm.add_custom_button(__('Pick List'), () => me.create_pick_list(), __('Create'));
						}

						let has_vehicles = me.frm.doc.items.some(d => d.is_vehicle);
						if (has_vehicles) {
							me.frm.add_custom_button(__('Reserved Vehicles'), () => me.create_reserved_vehicles(),
								__('Create'));
						}
					}

					// sales invoice
					if (flt(me.frm.doc.per_completed, 6) < 100) {
						if (frappe.model.can_create("Sales Invoice")) {
							me.frm.add_custom_button(__('Sales Invoice'), () => me.make_sales_invoice(), __('Create'));
						}
					}

					// material request
					if (frappe.model.can_create("Material Request")) {
						me.frm.add_custom_button(__('Material Request'), () => me.make_material_request(), __('Create'));
					}

					if (
						(!me.frm.doc.order_type || ["Sales", "Shopping Cart"].indexOf(me.frm.doc.order_type) !== -1)
						&& me.frm.doc.delivery_status == "To Deliver"
						&& frappe.model.can_create("Material Request")
					) {
						me.frm.add_custom_button(__('Request for Raw Materials'), () => me.make_raw_material_request(), __('Create'));
					}

					// make purchase order
					if (frappe.model.can_create("Purchase Order")) {
						me.frm.add_custom_button(__('Purchase Order'), () => me.make_purchase_order(), __('Create'));
					}

					// project
					// if (me.frm.doc.delivery_status == "To Deliver"
					// 	&& ["Sales", "Shopping Cart"].indexOf(me.frm.doc.order_type) !== -1
					// 	&& allow_delivery
					// 	&& frappe.model.can_create("Project")
					// ) {
					// 	me.frm.add_custom_button(__('Project'), () => me.make_project(), __('Create'));
					// }

					if(!me.frm.doc.auto_repeat && frappe.model.can_create("Auto Repeat")) {
						me.frm.add_custom_button(__('Subscription'), function() {
							erpnext.utils.make_subscription(me.frm.doc.doctype, me.frm.doc.name)
						}, __('Create'))
					}

					if (me.frm.doc.docstatus === 1 && !me.frm.doc.inter_company_reference) {
						if (me.frm.doc.__onload?.is_internal_customer) {
							me.frm.add_custom_button("Inter Company Order", function() {
								me.make_inter_company_order();
							}, __('Create'));
						}
					}
				}
				// payment request
				if(flt(me.frm.doc.per_billed) == 0) {
					if (frappe.model.can_create("Payment Request")) {
						me.frm.add_custom_button(__('Payment Request'), () => me.make_payment_request(), __('Create'));
					}

					if (frappe.model.can_create("Payment Entry") || frappe.model.can_create("Journal Entry")) {
						me.frm.add_custom_button(__('Payment'), () => me.make_payment_entry(), __('Create'));
					}
				}
				me.frm.page.set_inner_btn_group_as_primary(__('Create'));
			}
		}

		if (me.frm.doc.docstatus === 0) {
			if (frappe.model.can_read("Quotation")) {
				me.frm.add_custom_button(__('Quotation'), () => me.get_items_from_quotation(),
					__("Get Items From"));
			}

			me.add_get_applicable_items_button();
			me.add_get_service_template_items_button();
		}
	}

	get_items_from_quotation() {
		erpnext.utils.map_current_doc({
			method: "erpnext.selling.doctype.quotation.quotation.make_sales_order",
			source_doctype: "Quotation",
			target: this.frm,
			setters: [
				{
					label: "Customer",
					fieldname: "party_name",
					fieldtype: "Link",
					options: "Customer",
					default: this.frm.doc.customer || undefined
				},
				{
					fieldtype: 'Link',
					label: __('Project'),
					options: 'Project',
					fieldname: 'project',
					default: this.frm.doc.project || undefined,
				},
				{
					fieldtype: 'DateRange',
					label: __('Date Range'),
					fieldname: 'transaction_date',
				}
			],
			columns: ['customer_name', 'transaction_date', 'project'],
			get_query_filters: {
				company: this.frm.doc.company,
				docstatus: 1,
				status: ["!=", "Lost"]
			}
		})
	}

	setup_progressbars() {
		let has_work_order_qty = this.frm.doc.items.some(d => d.work_order_qty);
		if (this.frm.doc.docstatus == 1 && has_work_order_qty) {
			this.show_progress_for_production();
		}
	}

	show_progress_for_production() {
		let total_wo_qty = frappe.utils.sum(this.frm.doc.items.map(d => d.work_order_qty));
		let produced_qty = frappe.utils.sum(this.frm.doc.items.map(d => d.produced_qty));
		let remaining_qty = total_wo_qty - produced_qty;
		remaining_qty = Math.max(remaining_qty, 0);

		erpnext.utils.show_progress_for_qty({
			frm: this.frm,
			title: __('Production Status'),
			total_qty: total_wo_qty,
			progress_bars: [
				{
					title: __('<b>Produced:</b> {0}%', [
						format_number(produced_qty / total_wo_qty * 100, null, 1),
					]),
					completed_qty: produced_qty,
					progress_class: "progress-bar-success",
					add_min_width: 0.5,
				},
				{
					completed_qty: remaining_qty,
					progress_class: "progress-bar-warning",
				},
			],
		});
	}

	create_pick_list() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.create_pick_list",
			frm: this.frm
		})
	}

	make_work_orders() {
		return this.frm.call({
			method: "get_work_order_items",
			doc: this.frm.doc,
			callback: (r) => {
				if (!r.message || !r.message.length) {
					frappe.msgprint({
						title: __('Work Orders not created'),
						message: __('No pending Items with Bill of Materials to Manufacture'),
						indicator: 'orange'
					});
					return;
				}
				return erpnext.manufacturing.make_work_orders_from_order_dialog(r.message, this.frm.doc);
			}
		});
	}

	create_reserved_vehicles() {
		let me = this;
		if (me.frm.doc.docstatus !== 1) {
			return;
		}

		frappe.call({
			method: "erpnext.vehicles.doctype.vehicle.vehicle.get_sales_order_vehicle_qty",
			args: {
				sales_order: me.frm.doc.name
			},
			callback: function (r) {
				if (r.message) {
					const fields = [{
						label: 'Items',
						fieldtype: 'Table',
						fieldname: 'items',
						fields: [{
							fieldtype: 'Read Only',
							fieldname: 'item_code',
							label: __('Item Code'),
							in_list_view: 1
						}, {
							fieldtype: 'Float',
							fieldname: 'ordered_qty',
							read_only: 1,
							label: __('Ordered'),
							in_list_view: 1
						}, {
							fieldtype: 'Float',
							fieldname: 'reserved_qty',
							read_only: 1,
							label: __('Reserved'),
							in_list_view: 1
						}, {
							fieldtype: 'Float',
							fieldname: 'actual_qty',
							read_only: 1,
							label: __('In Stock'),
							in_list_view: 1
						}, {
							fieldtype: 'Int',
							fieldname: 'to_create_qty',
							label: __('To Create'),
							mandatory: 1,
							in_list_view: 1
						}],
						data: r.message,
						get_data: () => {
							return r.message
						}
					}];
					let dialog = new frappe.ui.Dialog({
						title: __('Quantity of Reserved Vehicles to create'),
						fields: fields,
						size: 'large',
						primary_action: function() {
							let data = dialog.get_values().items;
							let to_reserve_qty_map = {};
							$.each(data || [], function (i, d) {
								to_reserve_qty_map[d.item_code] = cint(d.to_create_qty);
							});
							frappe.call({
								method: 'erpnext.vehicles.doctype.vehicle.vehicle.create_vehicle_from_so',
								args: {
									sales_order: me.frm.doc.name,
									to_reserve_qty_map: to_reserve_qty_map
								},
								callback: function () {
									dialog.hide();
								},
								freeze: true
							});
						},
						primary_action_label: __('Create')
					});
					dialog.show();
				}
			}
		})
	}

	tc_name() {
		this.get_terms();
	}

	set_skip_delivery_note() {
		let items = (this.frm.doc.items || []).filter(d => d.item_code);
		if (!items.length) {
			this.frm.set_value('skip_delivery_note', 0);
			return;
		}

		let has_deliverable = items.some(d => !d.skip_delivery_note);
		this.frm.set_value('skip_delivery_note', cint(!has_deliverable));
	}

	make_material_request() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_material_request",
			frm: this.frm
		})
	}

	make_raw_material_request() {
		return this.frm.call({
			doc: this.frm.doc,
			method: "get_work_order_items",
			args: {
				for_raw_material_request: 1
			},
			callback: (r) => {
				if (!r.message || !r.message.length) {
					frappe.msgprint({
						message: __('No Items with Bill of Materials.'),
						indicator: 'orange'
					});
					return;
				}
				this.make_raw_material_request_dialog(r.message);
			}
		});
	}

	make_raw_material_request_dialog(items_data) {
		let dialog_doc = {
			for_warehouse: items_data && items_data[0].warehouse,
			include_exploded_items: 1,
			ignore_existing_ordered_qty: 0,
			items: items_data,
		}
		const fields = [
			{fieldname: "for_warehouse", label: __("For Warehouse"), fieldtype: "Link", options: "Warehouse", reqd: 1,
				get_query: () => erpnext.queries.warehouse(this.frm.doc)
			},
			{fieldtype: "Column Break"},
			{fieldname: "include_exploded_items", label: __("Include Exploded Items"), fieldtype: "Check"},
			{fieldname: "ignore_existing_ordered_qty", label: __("Ignore Existing Ordered Qty"), fieldtype: "Check"},
			{fieldtype: "Section Break"},
			{
				fieldname: "items",
				fieldtype: "Table",
				label: __("Items"),
				description: __('Select BOM and Qty'),
				fields: [
					{fieldname: "item_code", label: __("Item Code"), fieldtype: "Link", options: "Item", read_only: 1, reqd: 1, columns: 5, in_list_view: 1},
					{fieldname: "item_name", label: __("Item Name"), fieldtype: "Data", read_only: 1},
					{fieldname: "bom_no", label: __("BOM No"), fieldtype: "Link", options: "BOM", reqd: 1, columns: 3, in_list_view: 1,
						get_query: (doc) => { return { filters: {
							item: doc.item_code, is_active: 1, docstatus: 1,
						} } }
					},
					{fieldname: "required_qty", label: __("Qty"), fieldtype: "Float", reqd: 1, columns: 1, in_list_view: 1},
					{fieldname: "stock_uom", label: __("UOM"), fieldtype: "Data", read_only: 1, columns: 1, in_list_view: 1},
				],
				data: dialog_doc.items,
			}
		];

		let dialog = new frappe.ui.Dialog({
			title: __("Items for Raw Material Request"),
			fields: fields,
			size: "extra-large",
			doc: dialog_doc,
			primary_action: () => {
				let values = dialog.get_values();
				return frappe.call({
					method: "erpnext.selling.doctype.sales_order.sales_order.make_raw_material_request",
					args: {
						items: values,
						company: this.frm.doc.company,
						sales_order: this.frm.docname,
						project: this.frm.project
					},
					freeze: true,
					callback: (r) => {
						if (r.message) {
							frappe.model.sync(r.message);
							frappe.set_route("Form", r.message.doctype, r.message.name);
						}
						dialog.hide();
					}
				});
			},
			primary_action_label: __("Create")
		});
		dialog.show();
	}

	make_delivery_note_based_on(filters, packing_filter) {
		let item_grid = this.frm.fields_dict["items"].grid;
		let selected_rows = item_grid.get_selected();
		if (selected_rows.length) {
			selected_rows = (this.frm.doc.items || []).filter(d => selected_rows.includes(d.name));
		} else {
			selected_rows = this.frm.doc.items || [];
		}

		filters = filters || {};
		let filtered_items = frappe.utils.filter_dict(selected_rows, filters);
		let filtered_row_names = filtered_items.map(d => d.name);

		let warehouses = [];
		let delivery_dates = [];
		let has_packed_qty = false;
		$.each(filtered_items, function(i, d) {
			if(d.warehouse && !warehouses.includes(d.warehouse)) {
				warehouses.push(d.warehouse);
			}

			if(!delivery_dates.includes(d.delivery_date)) {
				delivery_dates.push(d.delivery_date);
			}

			if (flt(d.packed_qty) > 0) {
				has_packed_qty = true;
			}
		});

		if (has_packed_qty && !packing_filter) {
			return this.make_delivery_note_based_on_packing_type(filters);
		} else if (warehouses.length > 1 && !filters.warehouse) {
			return this.make_delivery_note_based_on_warehouse(warehouses, filters, packing_filter);
		} else if (delivery_dates.length > 1 && !filters.delivery_date) {
			return this.make_delivery_note_based_on_delivery_date(delivery_dates, filters, packing_filter);
		}

		if (filtered_items.length != this.frm.doc.items.length) {
			$.each(item_grid.grid_rows || [], function(j, row) {
				row.doc.__checked = filtered_row_names.includes(row.doc.name) ? 1 : 0;
			});
		}

		let warehouse = filters.warehouse && warehouses.length == 1 ? warehouses[0] : null;
		if (packing_filter && packing_filter != "Unpacked Items Only") {
			return this.make_delivery_note_from_packing_slips(packing_filter, warehouse);
		} else {
			return this.make_delivery_note(warehouse);
		}
	}

	make_delivery_note_based_on_packing_type(filters) {
		frappe.prompt({
			label: __("Deliver Packed or Unpacked Items"),
			fieldname: "type",
			fieldtype: "Select",
			options: ['', 'Packed Items Only', 'Unpacked Items Only', 'All Items'],
			reqd: 1
		}, (values) => {
			this.make_delivery_note_based_on(filters, values.type);
		}, __("Deliver Packed or Unpacked Items"), __("Select"));
	}

	make_delivery_note_based_on_warehouse(warehouses, filters, packing_filter) {
		var me = this;

		var dialog = new frappe.ui.Dialog({
			title: __("Select Items based on Warehouse"),
			fields: [{fieldtype: "HTML", fieldname: "warehouses_html"}]
		});

		var html = $(`
			<div style="border: 1px solid #d1d8dd">
				<div class="list-item list-item--head">
					<div class="list-item__content list-item__content--flex-2">
						${__('Warehouse')}
					</div>
				</div>
				${warehouses.map(warehouse => `
					<div class="list-item">
						<div class="list-item__content list-item__content--flex-2">
							<label>
							<input type="checkbox" data-warehouse="${warehouse}" checked="checked"/>
							${warehouse}
							</label>
						</div>
					</div>
				`).join("")}
			</div>
		`);

		var wrapper = dialog.fields_dict.warehouses_html.$wrapper;
		wrapper.html(html);

		dialog.set_primary_action(__("Select"), function() {
			var warehouses = wrapper.find('input[type=checkbox]:checked')
				.map((i, el) => $(el).attr('data-warehouse')).toArray();

			if(!warehouses) return;

			dialog.hide();

			filters["warehouse"] = ["in", warehouses]
			me.make_delivery_note_based_on(filters, packing_filter);
		});
		dialog.show();
	}

	make_delivery_note_based_on_delivery_date(delivery_dates, filters, packing_filter) {
		var me = this;

		var dialog = new frappe.ui.Dialog({
			title: __("Select Items based on Delivery Date"),
			fields: [{fieldtype: "HTML", fieldname: "dates_html"}]
		});

		var html = $(`
			<div style="border: 1px solid #d1d8dd">
				<div class="list-item list-item--head">
					<div class="list-item__content list-item__content--flex-2">
						${__('Delivery Date')}
					</div>
				</div>
				${delivery_dates.map(date => `
					<div class="list-item">
						<div class="list-item__content list-item__content--flex-2">
							<label>
							<input type="checkbox" data-date="${date}" checked="checked"/>
							${frappe.datetime.str_to_user(date)}
							</label>
						</div>
					</div>
				`).join("")}
			</div>
		`);

		var wrapper = dialog.fields_dict.dates_html.$wrapper;
		wrapper.html(html);

		dialog.set_primary_action(__("Select"), function() {
			var dates = wrapper.find('input[type=checkbox]:checked')
				.map((i, el) => $(el).attr('data-date')).toArray();

			if(!dates) return;

			dialog.hide();

			filters["delivery_date"] = ["in", dates];
			me.make_delivery_note_based_on(filters, packing_filter);
		});
		dialog.show();
	}

	make_delivery_note(warehouse) {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note",
			frm: this.frm,
			args: {
				warehouse: warehouse
			}
		})
	}

	make_delivery_note_from_packing_slips(packing_filter, warehouse) {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note_from_packing_slips",
			frm: this.frm,
			args: {
				packing_filter: packing_filter,
				warehouse: warehouse
			}
		})
	}

	make_packing_slip(warehouse) {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_packing_slip",
			frm: this.frm,
			args: {
				warehouse: warehouse
			}
		})
	}

	make_sales_invoice() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
			frm: this.frm
		})
	}

	make_project() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_project",
			frm: this.frm
		})
	}

	make_inter_company_order() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_inter_company_purchase_order",
			frm: this.frm
		});
	}

	make_purchase_invoice() {
		var me = this;
		var dialog = new frappe.ui.Dialog({
			title: __("Supplier"),
			fields: [
				{"fieldtype": "Link", "label": __("Supplier"), "fieldname": "supplier", "options":"Supplier", "reqd":true},
				{"fieldtype": "Button", "label": __("Make Purchase Invoice"), "fieldname": "make_purchase_invoice", "cssClass": "btn-primary"},
			]
		});

		dialog.fields_dict.make_purchase_invoice.$input.click(function() {
			var args = dialog.get_values();
			dialog.hide();
			return frappe.call({
				type: "GET",
				method: "erpnext.selling.doctype.sales_order.sales_order.make_purchase_invoice",
				args: {
					"supplier": args.supplier,
					"source_name": me.frm.doc.name
				},
				freeze: true,
				callback: function(r) {
					if(!r.exc) {
						var doc = frappe.model.sync(r.message);
						frappe.set_route("Form", r.message.doctype, r.message.name);
					}
				}
			})
		});
		dialog.show();
	}

	make_purchase_order() {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_purchase_order",
			frm: this.frm,
		});
	}

	hold_sales_order() {
		var me = this;
		var d = new frappe.ui.Dialog({
			title: __('Reason for Hold'),
			fields: [
				{
					"fieldname": "reason_for_hold",
					"fieldtype": "Text",
					"reqd": 1,
				}
			],
			primary_action: function() {
				var data = d.get_values();
				frappe.call({
					method: "frappe.desk.form.utils.add_comment",
					args: {
						reference_doctype: me.frm.doctype,
						reference_name: me.frm.docname,
						content: __('Reason for hold: ')+data.reason_for_hold,
						comment_email: frappe.session.user
					},
					callback: function(r) {
						if(!r.exc) {
							me.update_status('Hold', 'On Hold')
							d.hide();
						}
					}
				});
			}
		});
		d.show();
	}

	close_sales_order() {
		this.update_status("Close", "Closed")
	}

	update_status(label, status) {
		var doc = this.frm.doc;
		var me = this;
		frappe.ui.form.is_saving = true;
		frappe.call({
			method: "erpnext.selling.doctype.sales_order.sales_order.update_status",
			args: {status: status, name: doc.name},
			callback: function(r){
				me.frm.reload_doc();
			},
			always: function() {
				frappe.ui.form.is_saving = false;
			}
		});
	}
};
extend_cscript(cur_frm.cscript, new erpnext.selling.SalesOrderController({frm: cur_frm}));
