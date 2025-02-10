// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.buying");

{% include 'erpnext/public/js/controllers/buying.js' %};

frappe.ui.form.on("Purchase Order", {
	setup: function(frm) {

		frm.set_query("reserve_warehouse", "supplied_items", function() {
			return {
				filters: {
					"company": frm.doc.company,
					"name": ['!=', frm.doc.supplier_warehouse],
					"is_group": 0
				}
			}
		});

	},

	onload: function(frm) {
		if (!frm.doc.transaction_date){
			frm.set_value('transaction_date', frappe.datetime.get_today())
		}

		if (frm.doc.__islocal) {
			frm.events.schedule_date(frm);
		}

		erpnext.queries.setup_queries(frm, "Warehouse", function() {
			return erpnext.queries.warehouse(frm.doc);
		});
	},

	schedule_date: function (frm) {
		if (frm.doc.schedule_date) {
			$.each(frm.doc.items || [], function (i, d) {
				d.schedule_date = frm.doc.schedule_date;
			});
			refresh_field("items");
		}
	}
});

frappe.ui.form.on("Purchase Order Item", {
	item_code: function(frm,cdt,cdn) {
		var row = locals[cdt][cdn];
		if (frm.doc.schedule_date) {
			row.schedule_date = frm.doc.schedule_date;
			refresh_field("schedule_date", cdn, "items");
		} else {
			frm.script_manager.copy_from_first_row("items", row, ["schedule_date"]);
		}
	},
	schedule_date: function(frm, cdt, cdn) {
		if(!frm.doc.schedule_date) {
			erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "items", "schedule_date");
		}
	}
});

erpnext.buying.PurchaseOrderController = class PurchaseOrderController extends erpnext.buying.BuyingController {
	setup() {
		this.frm.custom_make_buttons = {
			'Purchase Receipt': 'Purchase Receipt',
			'Purchase Invoice': 'Purchase Invoice',
			'Stock Entry': 'Materials to Supplier',
			'Packing Slip': 'Packing Slip',
			'Payment Entry': 'Payment',
			'Auto Repeat': 'Subscription',
		}

		super.setup();
	}

	refresh(doc, cdt, cdn) {
		var me = this;
		super.refresh();
		var allow_receipt = false;
		var is_drop_ship = false;

		for (var i in cur_frm.doc.items) {
			var item = cur_frm.doc.items[i];
			if(item.delivered_by_supplier !== 1) {
				allow_receipt = true;
			} else {
				is_drop_ship = true;
			}

			if(is_drop_ship && allow_receipt) {
				break;
			}
		}

		this.frm.set_df_property("drop_ship", "hidden", !is_drop_ship);

		if (me.frm.doc.docstatus == 0) {
			me.add_get_latest_price_button();

			this.frm.add_custom_button(__('Set Price as Last Purchase Rate'), () => {
				this.frm.call({
					method: "get_last_purchase_rate",
					doc: this.frm.doc,
					callback: () => {
						this.frm.dirty();
						this.calculate_taxes_and_totals();
					}
				});
			}, __("Prices"));
		}
		if (me.frm.doc.docstatus == 1) {
			me.add_update_price_list_button();
		}

		if(doc.docstatus == 1) {
			if(!in_list(["Closed", "Delivered"], doc.status)) {
				if (
					this.frm.doc.status !== 'Closed'
					&& this.frm.doc.receipt_status == "To Receive"
					&& this.frm.doc.billing_status == "To Bill"
				) {
					this.frm.add_custom_button(__('Update Items'), () => {
						erpnext.utils.update_child_items({
							frm: this.frm,
							child_docname: "items",
							child_doctype: "Purchase Order Detail",
							cannot_add_row: false,
						})
					});
				}
				if (this.frm.has_perm("submit")) {
					if(doc.billing_status == "To Bill" || doc.receipt_status == "To Receive") {
						if (doc.status != "On Hold") {
							this.frm.add_custom_button(__('Hold'), () => this.hold_purchase_order(), __("Status"));
						} else {
							this.frm.add_custom_button(__('Resume'), () => this.unhold_purchase_order(), __("Status"));
						}
						this.frm.add_custom_button(__('Close'), () => this.close_purchase_order(), __("Status"));
					}
				}

				if (is_drop_ship && doc.status!="Delivered") {
					this.frm.add_custom_button(__('Delivered'), this.delivered_by_supplier,
						__("Status"));

					this.frm.page.set_inner_btn_group_as_primary(__("Status"));
				}
			} else if (in_list(["Closed", "Delivered"], doc.status)) {
				if (this.frm.has_perm("submit")) {
					this.frm.add_custom_button(__('Re-Open'), () => this.unclose_purchase_order(), __("Status"));
				}
			}

			if (doc.status != "Closed") {
				if (doc.status != "On Hold") {
					if(flt(doc.per_received, 6) < 100 && allow_receipt) {
						if (doc.is_subcontracted && me.has_unsupplied_items()) {
							if (frappe.model.can_create("Stock Entry")) {
								this.frm.add_custom_button(__('Materials to Supplier'), () => me.make_rm_stock_entry(),
									__("Subcontract"));
							}

							if (frappe.model.can_create("Packing Slip")) {
								this.frm.add_custom_button(__('Packing Slip'), () => me.make_packing_slip(),
									__("Subcontract"));
							}
						}

						if (frappe.model.can_create("Purchase Receipt")) {
							this.frm.add_custom_button(__('Purchase Receipt'), () => this.make_purchase_receipt_based_on(),
								__('Create'));
						}
					}

					if (flt(doc.per_completed, 6) < 100 && frappe.model.can_create("Purchase Invoice")) {
						this.frm.add_custom_button(__('Purchase Invoice'), this.make_purchase_invoice,
							__('Create'));
					}

					if (!doc.auto_repeat && frappe.model.can_create("Auto Repeat")) {
						this.frm.add_custom_button(__('Subscription'), function() {
							erpnext.utils.make_subscription(doc.doctype, doc.name)
						}, __('Create'))
					}

					if (doc.docstatus === 1 && !doc.inter_company_reference) {
						if (me.frm.doc.__onload?.is_internal_supplier) {
							me.frm.add_custom_button("Inter Company Order", function() {
								me.make_inter_company_order(me.frm);
							}, __('Create'));
						}
					}
				}

				if (flt(doc.per_billed) == 0 && frappe.model.can_create("Payment Request")) {
					this.frm.add_custom_button(__('Payment Request'), () => me.make_payment_request(),
						__('Create'));
				}

				if (
					flt(doc.per_billed) == 0
					&& doc.status != "Delivered"
					&& (frappe.model.can_create("Purchase Entry") || frappe.model.can_create("Journal Entry"))
				) {
					this.frm.add_custom_button(__('Payment'), this.frm.cscript.make_payment_entry,
						__('Create'));
				}

				this.frm.page.set_inner_btn_group_as_primary(__('Create'));
			}
		} else if(doc.docstatus===0) {
			me.add_from_mappers();
		}

		this.frm.set_indicator_formatter('item_code', function(doc) {
			if (doc.docstatus === 1) {
				if (!doc.received_qty) {
					if (!doc.is_stock_item && !doc.is_fixed_asset) {
						return "purple";
					} else {
						return "orange";
					}
				} else if (doc.received_qty < doc.qty) {
					return "yellow";
				} else {
					return "green";
				}
			}
		});
	}

	get_items_from_open_material_requests() {
		erpnext.utils.map_current_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_purchase_order_based_on_supplier",
			source_name: this.frm.doc.supplier,
			get_query_filters: {
				docstatus: ["!=", 2],
			}
		});
	}

	validate() {
		set_schedule_date(this.frm);
	}

	has_unsupplied_items() {
		return this.frm.doc['supplied_items'].some(item => {
			let qty_precision = precision("required_qty", item);
			return flt(item.supplied_qty, qty_precision) < flt(item.required_qty, qty_precision);
		})
	}

	make_rm_stock_entry() {
		return frappe.call({
			method: "erpnext.buying.doctype.purchase_order.purchase_order.make_rm_stock_entry",
			args: {
				purchase_order: this.frm.doc.name,
			},
			callback: function(r) {
				let doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		});
	}

	make_packing_slip() {
		return frappe.call({
			method: "erpnext.buying.doctype.purchase_order.purchase_order.make_packing_slip",
			args: {
				purchase_order: this.frm.doc.name,
			},
			callback: function(r) {
				let doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		});
	}

	make_inter_company_order(frm) {
		frappe.model.open_mapped_doc({
			method: "erpnext.buying.doctype.purchase_order.purchase_order.make_inter_company_sales_order",
			frm: frm
		});
	}

	make_purchase_receipt_based_on(filters) {
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
		for (let d of filtered_items) {
			if (d.warehouse && !warehouses.includes(d.warehouse)) {
				warehouses.push(d.warehouse);
			}
		}

		if (warehouses.length > 1 && !filters.warehouse) {
			return this.make_purchase_receipt_based_on_warehouse(warehouses, filters);
		}

		if (filtered_items.length != this.frm.doc.items.length) {
			$.each(item_grid.grid_rows || [], function(j, row) {
				row.doc.__checked = filtered_row_names.includes(row.doc.name) ? 1 : 0;
			});
		}

		let warehouse = warehouses.length == 1 ? warehouses[0] : null;
		return this.make_purchase_receipt(warehouse);
	}

	make_purchase_receipt_based_on_warehouse(warehouses, filters) {
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
			me.make_purchase_receipt_based_on(filters);
		});
		dialog.show();
	}

	make_purchase_receipt(warehouse) {
		frappe.model.open_mapped_doc({
			method: "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
			frm: cur_frm,
			args: {
				warehouse: warehouse
			},
		})
	}

	make_purchase_invoice() {
		frappe.model.open_mapped_doc({
			method: "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
			frm: cur_frm
		})
	}

	add_from_mappers() {
		var me = this;

		this.frm.add_custom_button(__('Link to Material Request'), function() {
			var my_items = [];
			for (var i in me.frm.doc.items) {
				if(!me.frm.doc.items[i].material_request){
					my_items.push(me.frm.doc.items[i].item_code);
				}
			}
			frappe.call({
				method: "erpnext.buying.utils.get_linked_material_requests",
				args:{
					items: my_items
				},
				callback: function(r) {
					if(r.exc) return;

					var i = 0;
					var item_length = me.frm.doc.items.length;
					while (i < item_length) {
						var qty = me.frm.doc.items[i].qty;
						(r.message[0] || []).forEach(function(d) {
							if (d.qty > 0 && qty > 0 && me.frm.doc.items[i].item_code == d.item_code && !me.frm.doc.items[i].material_request_item)
							{
								me.frm.doc.items[i].material_request = d.mr_name;
								me.frm.doc.items[i].material_request_item = d.mr_item;
								var my_qty = Math.min(qty, d.qty);
								qty = qty - my_qty;
								d.qty = d.qty  - my_qty;
								me.frm.doc.items[i].stock_qty = my_qty * me.frm.doc.items[i].conversion_factor;
								me.frm.doc.items[i].qty = my_qty;

								frappe.msgprint("Assigning " + d.mr_name + " to " + d.item_code + " (row " + me.frm.doc.items[i].idx + ")");
								if (qty > 0) {
									frappe.msgprint("Splitting " + qty + " units of " + d.item_code);
									var new_row = frappe.model.add_child(me.frm.doc, me.frm.doc.items[i].doctype, "items");
									item_length++;

									for (var key in me.frm.doc.items[i]) {
										new_row[key] = me.frm.doc.items[i][key];
									}

									new_row.idx = item_length;
									new_row["stock_qty"] = new_row.conversion_factor * qty;
									new_row["qty"] = qty;
									new_row["material_request"] = "";
									new_row["material_request_item"] = "";
								}
							}
						});
						i++;
					}
					refresh_field("items");
				}
			});
		}, __("Tools"));

		if (frappe.model.can_read("Material Request")) {
			this.frm.add_custom_button(__('Material Request'), function () {
				erpnext.utils.map_current_doc({
					method: "erpnext.stock.doctype.material_request.material_request.make_purchase_order",
					source_doctype: "Material Request",
					target: me.frm,
					setters: [
						{
							fieldtype: 'Link',
							label: __('Project'),
							options: 'Project',
							fieldname: 'project',
							default: me.frm.doc.project || undefined,
						},
						{
							fieldtype: 'DateRange',
							label: __('Date Range'),
							fieldname: 'transaction_date',
						}
					],
					columns: ['project', 'transaction_date'],
					get_query_filters: {
						company: me.frm.doc.company,
						docstatus: 1,
						material_request_type: "Purchase",
						status: ["!=", "Stopped"],
						order_status: "To Order",
					}
				})
			}, __("Get Items From"));
		}

		if (frappe.model.can_read("Supplier Quotation")) {
			this.frm.add_custom_button(__('Supplier Quotation'), function () {
				erpnext.utils.map_current_doc({
					method: "erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order",
					source_doctype: "Supplier Quotation",
					target: me.frm,
					setters: {
						company: me.frm.doc.company
					},
					get_query_filters: {
						docstatus: 1,
						status: ["!=", "Stopped"],
					}
				})
			}, __("Get Items From"));
		}

		this.set_from_product_bundle();
	}

	tc_name() {
		this.get_terms();
	}

	unhold_purchase_order() {
		cur_frm.cscript.update_status("Resume", "Draft")
	}

	hold_purchase_order() {
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

	unclose_purchase_order() {
		cur_frm.cscript.update_status('Re-Open', 'Submitted')
	}

	close_purchase_order() {
		cur_frm.cscript.update_status('Close', 'Closed')
	}

	delivered_by_supplier() {
		cur_frm.cscript.update_status('Deliver', 'Delivered')
	}
};

// for backward compatibility: combine new and previous states
extend_cscript(cur_frm.cscript, new erpnext.buying.PurchaseOrderController({frm: cur_frm}));

cur_frm.cscript.update_status= function(label, status){
	frappe.call({
		method: "erpnext.buying.doctype.purchase_order.purchase_order.update_status",
		args: {status: status, name: cur_frm.doc.name},
		callback: function(r) {
			cur_frm.set_value("status", status);
			cur_frm.reload_doc();
		}
	})
}

cur_frm.fields_dict['items'].grid.get_field('project').get_query = function(doc, cdt, cdn) {
	return {
		filters:[
			['Project', 'status', 'not in', 'Completed, Cancelled']
		]
	}
}

cur_frm.fields_dict['items'].grid.get_field('bom').get_query = function(doc, cdt, cdn) {
	var d = locals[cdt][cdn]
	return {
		filters: [
			['BOM', 'item', '=', d.item_code],
			['BOM', 'is_active', '=', '1'],
			['BOM', 'docstatus', '=', '1'],
			['BOM', 'company', '=', doc.company]
		]
	}
}

function set_schedule_date(frm) {
	if(frm.doc.schedule_date){
		erpnext.utils.copy_value_in_all_rows(frm.doc, frm.doc.doctype, frm.doc.name, "items", "schedule_date");
	}
}

frappe.provide("erpnext.buying");

frappe.ui.form.on("Purchase Order", "is_subcontracted", function(frm) {
	if (frm.doc.is_subcontracted) {
		erpnext.buying.get_default_bom(frm);
		erpnext.buying.set_default_supplier_warehouse(frm);
	}
});
