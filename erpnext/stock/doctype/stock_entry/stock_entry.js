// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors // License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.stock");

frappe.ui.form.on('Stock Entry', {
	setup: function(frm) {
		frm.set_query('work_order', function() {
			let filters = {
				'docstatus': 1,
				'company': frm.doc.company,
				'status': ['!=', 'Completed']
			};
			if (frm.doc.purpose == "Material Transfer for Manufacture") {
				filters['per_material_transferred'] = ['<', 100];
				filters['skip_transfer'] = 0;
			} else {
				filters['per_produced'] = ['<', 100];
			}
			return {
				filters: filters
			}
		});

		frm.set_query('outgoing_stock_entry', function() {
			return {
				filters: [
					['Stock Entry', 'docstatus', '=', 1],
					['Stock Entry', 'per_transferred', '<','100'],
					['Stock Entry', 'purpose', '=', 'Send to Warehouse']
				]
			}
		});

		frm.set_query('batch_no', 'items', function(doc, cdt, cdn) {
			var item = locals[cdt][cdn];
			if(!item.item_code) {
				frappe.throw(__("Please enter Item Code to get Batch Number"));
			} else {
				if (item.s_warehouse || frm.doc.purpose == "Material Receipt") {
					return {
						query : "erpnext.controllers.queries.get_batch_no",
						filters: {
							item_code: item.item_code,
							warehouse: item.s_warehouse || item.t_warehouse,
							posting_date: frm.doc.posting_date || frappe.datetime.nowdate()
						}
					};
				} else {
					return {
						filters: {
							item: item.item_code
						}
					}
				}
			}
		});

		frm.set_query('vehicle', 'items', function(doc, cdt, cdn) {
			var item = frappe.get_doc(cdt, cdn);
			if (!item.item_code) {
				frappe.throw(__("Please select Item first then select Vehicle"))
			}

			var filters = {};
			filters.item_code = item.item_code;

			if (doc.customer) {
				filters['customer'] = ['in', [doc.customer, '']];
			}

			if (item.s_warehouse) {
				filters['warehouse'] = item.s_warehouse;
			} else if (item.t_warehouse) {
				filters['warehouse'] = ['is', 'not set'];
				filters['purchase_document_no'] = ['is', 'not set'];
			}

			return {
				filters: filters
			}
		});

		frm.set_query("expense_account", "additional_costs", function() {
			return {
				query: "erpnext.controllers.queries.tax_account_query",
				filters: {
					"account_type": ["Tax", "Chargeable", "Income Account", "Expenses Included In Valuation", "Expenses Included In Asset Valuation"],
					"company": frm.doc.company
				}
			};
		});

		frm.add_fetch("bom_no", "inspection_required", "inspection_required");
	},

	setup_quality_inspection: function(frm) {
		if (!frm.doc.inspection_required) {
			return;
		}

		let quality_inspection_field = frm.get_docfield("items", "quality_inspection");
		quality_inspection_field.get_route_options_for_new_doc = function(row) {
			if (frm.is_new()) return;
			return {
				"inspection_type": "Incoming",
				"reference_type": frm.doc.doctype,
				"reference_name": frm.doc.name,
				"item_code": row.doc.item_code,
				"description": row.doc.description,
				"item_serial_no": row.doc.serial_no ? row.doc.serial_no.split("\n")[0] : null,
				"batch_no": row.doc.batch_no
			}
		}

		frm.set_query("quality_inspection", "items", function(doc, cdt, cdn) {
			var d = locals[cdt][cdn];

			return {
				query:"erpnext.stock.doctype.quality_inspection.quality_inspection.quality_inspection_query",
				filters: {
					'item_code': d.item_code,
					'reference_name': doc.name
				}
			}
		});
	},

	outgoing_stock_entry: function(frm) {
		frappe.call({
			doc: frm.doc,
			method: "set_items_for_stock_in",
			callback: function() {
				refresh_field('items');
			}
		});
	},

	purpose: function(frm) {
		frm.fields_dict.items.grid.refresh();
		frm.cscript.toggle_related_fields(frm.doc);
		frm.cscript.show_hide_select_batch_button();
	},

	customer_provided: function(frm) {
		frm.cscript.toggle_related_fields(frm.doc);
	},

	company: function(frm) {
		if(frm.doc.company) {
			var company_doc = frappe.get_doc(":Company", frm.doc.company);
			if(company_doc.default_letter_head) {
				frm.set_value("letter_head", company_doc.default_letter_head);
			}
			frm.trigger("toggle_display_account_head");
		}
	},

	set_serial_no: function(frm, cdt, cdn, callback) {
		var d = frappe.model.get_doc(cdt, cdn);
		if(!d.item_code && !d.s_warehouse && !d.qty) return;
		var	args = {
			'item_code'	: d.item_code,
			'warehouse'	: cstr(d.s_warehouse),
			'stock_qty'		: d.stock_qty
		};
		frappe.call({
			method: "erpnext.stock.get_item_details.get_serial_no",
			args: {"args": args},
			callback: function(r) {
				if (!r.exe && r.message){
					frappe.model.set_value(cdt, cdn, "serial_no", r.message);
				}
				if (callback) {
					callback();
				}
			}
		});
	},

	auto_select_batches: function(frm) {
		return frm.call({
			method: 'auto_select_batches',
			doc: frm.doc,
			freeze: 1,
			callback: function (r) {
				frm.refresh_fields();
				frm.dirty();
			}
		});
	},

	make_retention_stock_entry: function(frm) {
		frappe.call({
			method: "erpnext.stock.doctype.stock_entry.stock_entry.move_sample_to_retention_warehouse",
			args:{
				"company": frm.doc.company,
				"items": frm.doc.items
			},
			callback: function (r) {
				if (r.message) {
					var doc = frappe.model.sync(r.message)[0];
					frappe.set_route("Form", doc.doctype, doc.name);
				}
				else {
					frappe.msgprint(__("Retention Stock Entry already created or Sample Quantity not provided"));
				}
			}
		});
	},

	toggle_display_account_head: function(frm) {
		var enabled = erpnext.is_perpetual_inventory_enabled(frm.doc.company);
		frm.fields_dict["items"].grid.set_column_disp(["cost_center", "expense_account"], enabled);
	},

	set_basic_rate: function(frm, cdt, cdn) {
		const item = locals[cdt][cdn];

		if (cint(frm.doc.customer_provided)) {
			frappe.model.set_value(cdt, cdn, 'basic_rate', 0.0);
			frm.events.calculate_basic_amount(frm, item);
		} else {
			const args = {
				'item_code': item.item_code,
				'posting_date': frm.doc.posting_date,
				'posting_time': frm.doc.posting_time,
				'warehouse': cstr(item.s_warehouse) || cstr(item.t_warehouse),
				'batch_no': item.batch_no,
				'serial_no': item.serial_no,
				'company': frm.doc.company,
				'qty': item.s_warehouse ? -1 * flt(item.stock_qty) : flt(item.stock_qty),
				'voucher_type': frm.doc.doctype,
				'voucher_no': item.name,
				'allow_zero_valuation': 1,
			};

			if (item.item_code || item.serial_no) {
				frappe.call({
					method: "erpnext.stock.utils.get_incoming_rate",
					args: {
						args: args
					},
					callback: function (r) {
						frappe.model.set_value(cdt, cdn, 'basic_rate', (r.message || 0.0));
						frm.events.calculate_basic_amount(frm, item);
					}
				});
			}
		}
	},

	calculate_total_qty(frm) {
		frm.doc.total_qty = 0;
		frm.doc.total_stock_qty = 0;
		frm.doc.total_alt_uom_qty = 0;

		let has_target_warehouse = (frm.doc.items || []).some(d => d.t_warehouse);

		$.each(frm.doc.items || [], function (i, item) {
			item.stock_qty = flt(flt(item.qty) * flt(item.conversion_factor), 6);

			if (!item.alt_uom) {
				item.alt_uom_size = 1.0;
			}
			item.alt_uom_qty = flt(flt(item.qty) * flt(item.conversion_factor) * flt(item.alt_uom_size),
				precision("alt_uom_qty", item));

			if (!has_target_warehouse || item.t_warehouse) {
				frm.doc.total_qty += flt(item.qty)
				frm.doc.total_stock_qty += flt(item.stock_qty)
				frm.doc.total_alt_uom_qty += flt(item.alt_uom_qty)
			}
		});

		frm.doc.total_qty = flt(frm.doc.total_qty, precision("total_qty"));
		frm.doc.total_stock_qty = flt(frm.doc.total_stock_qty, precision("total_stock_qty"));
		frm.doc.total_alt_uom_qty = flt(frm.doc.total_alt_uom_qty, precision("total_alt_uom_qty"));

		frappe.model.round_floats_in(frm.doc, ["total_qty", "total_stock_qty", "total_alt_uom_qty"]);
		frm.refresh_field("total_qty");
		frm.refresh_field("total_stock_qty");
		frm.refresh_field("total_alt_uom_qty");
	},

	get_warehouse_details: function(frm, cdt, cdn) {
		var child = locals[cdt][cdn];
		if(!child.bom_no) {
			frappe.call({
				method: "erpnext.stock.doctype.stock_entry.stock_entry.get_warehouse_details",
				args: {
					"args": {
						'item_code': child.item_code,
						'warehouse': cstr(child.s_warehouse) || cstr(child.t_warehouse),
						'batch_no': child.batch_no,
						'stock_qty': child.stock_qty,
						'serial_no': child.serial_no,
						'qty': child.s_warehouse ? -1* child.stock_qty : child.stock_qty,
						'posting_date': frm.doc.posting_date,
						'posting_time': frm.doc.posting_time,
						'company': frm.doc.company,
						'voucher_type': frm.doc.doctype,
						'voucher_no': child.name,
						'customer_provided': cint(frm.doc.customer_provided),
						'allow_zero_valuation': 1
					}
				},
				callback: function(r) {
					if (!r.exc) {
						$.extend(child, r.message);
						frm.events.calculate_basic_amount(frm, child);
					}
				}
			});
		}
	},

	calculate_basic_amount: function(frm, item) {
		item.basic_amount = flt(flt(item.stock_qty) * flt(item.basic_rate),
			precision("basic_amount", item));

		frm.events.calculate_amount(frm);
	},

	calculate_amount: function(frm) {
		frm.events.calculate_total_additional_costs(frm);

		const total_basic_amount = frappe.utils.sum(
			(frm.doc.items || []).map(function(i) { return i.t_warehouse ? flt(i.basic_amount) : 0; })
		);

		for (let i in frm.doc.items) {
			let item = frm.doc.items[i];

			if (item.t_warehouse && total_basic_amount) {
				item.additional_cost = (flt(item.basic_amount) / total_basic_amount) * frm.doc.total_additional_costs;
			} else {
				item.additional_cost = 0;
			}

			item.amount = flt(item.basic_amount + flt(item.additional_cost),
				precision("amount", item));

			if (flt(item.stock_qty)) {
				item.valuation_rate = item.amount / flt(item.stock_qty);
			}
		}

		refresh_field('items');
	},

	calculate_total_additional_costs: function(frm) {
		const total_additional_costs = frappe.utils.sum(
			(frm.doc.additional_costs || []).map(function(c) { return flt(c.amount); })
		);

		frm.set_value("total_additional_costs",
			flt(total_additional_costs, precision("total_additional_costs")));
	},

	source_warehouse_address: function(frm) {
		erpnext.utils.get_address_display(frm, 'source_warehouse_address', 'source_address_display', false);
	},

	target_warehouse_address: function(frm) {
		erpnext.utils.get_address_display(frm, 'target_warehouse_address', 'target_address_display', false);
	},

	customer_address: function(frm) {
		erpnext.utils.get_address_display(frm, 'customer_address', 'address_display', false);
	},

	supplier_address: function(frm) {
		erpnext.utils.get_address_display(frm, 'supplier_address', 'address_display', false);
	},

	project: function (frm) {
		frm.cscript.set_item_cost_centers();
	},
});

frappe.ui.form.on('Stock Entry Detail', {
	qty: function(frm, cdt, cdn) {
		frm.events.set_serial_no(frm, cdt, cdn, () => {
			frm.events.calculate_total_qty(frm);
			frm.events.set_basic_rate(frm, cdt, cdn);
		});
	},

	conversion_factor: function(frm, cdt, cdn) {
		frm.events.calculate_total_qty(frm);
		frm.events.set_basic_rate(frm, cdt, cdn);
	},

	s_warehouse: function(frm, cdt, cdn) {
		frm.events.set_serial_no(frm, cdt, cdn, () => {
			frm.events.get_warehouse_details(frm, cdt, cdn);
		});
		frm.cscript.show_hide_select_batch_button();
	},

	t_warehouse: function(frm, cdt, cdn) {
		frm.events.calculate_total_qty(frm);
		frm.events.get_warehouse_details(frm, cdt, cdn);
	},

	basic_rate: function(frm, cdt, cdn) {
		var item = locals[cdt][cdn];

		if (cint(frm.doc.customer_provided)) {
			item.basic_rate = 0;
		}

		frm.events.calculate_basic_amount(frm, item);
	},

	barcode: function(doc, cdt, cdn) {
		var d = locals[cdt][cdn];
		if (d.barcode) {
			frappe.call({
				method: "erpnext.stock.get_item_details.get_item_code",
				args: {"barcode": d.barcode },
				callback: function(r) {
					if (!r.exe){
						frappe.model.set_value(cdt, cdn, "item_code", r.message);
					}
				}
			});
		}
	},

	uom: function(doc, cdt, cdn) {
		var d = locals[cdt][cdn];
		if(d.uom && d.item_code){
			return frappe.call({
				method: "erpnext.stock.doctype.stock_entry.stock_entry.get_uom_details",
				args: {
					item_code: d.item_code,
					uom: d.uom,
					qty: d.qty
				},
				callback: function(r) {
					if(r.message) {
						frappe.model.set_value(cdt, cdn, r.message);
					}
				}
			});
		}
	},

	item_code: function(frm, cdt, cdn) {
		var d = locals[cdt][cdn];
		if(d.item_code) {
			var args = {
				'item_code': d.item_code,
				'uom': d.uom,
				'hide_item_code': d.hide_item_code,
				'warehouse': cstr(d.s_warehouse) || cstr(d.t_warehouse),
				'stock_qty': d.stock_qty,
				'serial_no': d.serial_no,
				'bom_no': d.bom_no,
				'expense_account': d.expense_account,
				'cost_center': d.cost_center,
				'project': d.project || frm.doc.project,
				'company': frm.doc.company,
				'qty': d.qty,
				'voucher_type': frm.doc.doctype,
				'voucher_no': d.name,
				'customer_provided': cint(frm.doc.customer_provided),
				'allow_zero_valuation': 1,
				'stock_entry_type': frm.doc.stock_entry_type,
				'posting_date': frm.doc.posting_date,
				'posting_time': frm.doc.posting_time,
			};

			return frappe.call({
				doc: frm.doc,
				method: "get_item_details",
				args: args,
				callback: function(r) {
					if(r.message) {
						var d = locals[cdt][cdn];
						$.each(r.message, function(k, v) {
							frappe.model.set_value(cdt, cdn, k, v); // qty and it's subsequent fields weren't triggered
						});
						frm.cscript.show_hide_select_batch_button();
						refresh_field("items");
					}
				}
			});
		} else {
			frm.cscript.show_hide_select_batch_button();
		}
	},

	project: function (frm, cdt, cdn) {
		if (cdt && cdn) {
			frm.cscript.set_item_cost_centers(cdn);
		}
	},

	expense_account: function(frm, cdt, cdn) {
		erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "items", "expense_account");
	},
	cost_center: function(frm, cdt, cdn) {
		erpnext.utils.copy_value_in_all_rows(frm.doc, cdt, cdn, "items", "cost_center");
	},
	sample_quantity: function(frm, cdt, cdn) {
		validate_sample_quantity(frm, cdt, cdn);
	},
	batch_no: function(frm, cdt, cdn) {
		validate_sample_quantity(frm, cdt, cdn);
	},
});

var validate_sample_quantity = function(frm, cdt, cdn) {
	var d = locals[cdt][cdn];
	if (d.sample_quantity && frm.doc.purpose == "Material Receipt") {
		frappe.call({
			method: 'erpnext.stock.doctype.stock_entry.stock_entry.validate_sample_quantity',
			args: {
				batch_no: d.batch_no,
				item_code: d.item_code,
				sample_quantity: d.sample_quantity,
				qty: d.stock_qty
			},
			callback: (r) => {
				frappe.model.set_value(cdt, cdn, "sample_quantity", r.message);
			}
		});
	}
};

frappe.ui.form.on('Stock Entry Taxes and Charges', {
	amount: function(frm) {
		frm.events.calculate_amount(frm);
	}
});

erpnext.stock.StockEntry = class StockEntry extends erpnext.stock.StockController {
	setup() {
		super.setup();

		var me = this;

		this.setup_posting_date_time_check();

		erpnext.queries.setup_warehouse_qty_query(me.frm, "s_warehouse");

		this.frm.fields_dict.bom_no.get_query = function() {
			return {
				filters:{
					"docstatus": 1,
					"is_active": 1
				}
			};
		};

		if (this.frm.fields_dict["cost_center"]) {
			this.frm.set_query("cost_center", function(doc) {
				return {
					filters: {
						"company": doc.company,
						"is_group": 0
					}
				};
			});
		}

		this.frm.fields_dict.items.grid.get_field('item_code').get_query = function() {
			return erpnext.queries.item({is_stock_item: 1});
		};

		this.frm.set_query("purchase_order", function() {
			return {
				"filters": {
					"docstatus": 1,
					"is_subcontracted": 1,
					"company": me.frm.doc.company
				}
			};
		});

		if(me.frm.doc.company && erpnext.is_perpetual_inventory_enabled(me.frm.doc.company)) {
			this.frm.add_fetch("company", "stock_adjustment_account", "expense_account");
		}

		this.frm.fields_dict.items.grid.get_field('expense_account').get_query = function() {
			if (erpnext.is_perpetual_inventory_enabled(me.frm.doc.company)) {
				return {
					filters: {
						"company": me.frm.doc.company,
						"is_group": 0
					}
				}
			}
		}

		this.frm.set_query("uom", "items", function(doc, cdt, cdn) {
			let item = frappe.get_doc(cdt, cdn);
			return erpnext.queries.item_uom(item.item_code);
		});

		this.frm.set_query("subcontracted_item", "items", function() {
			return erpnext.queries.subcontracted_item(me.frm.doc.purchase_order);
		});

		this.frm.add_fetch("purchase_order", "supplier", "supplier");

		this.frm.set_query("supplier_address", function() {
			frappe.dynamic_link = { doc: me.frm.doc, fieldname: 'supplier', doctype: 'Supplier' };
			return erpnext.queries.address_query(me.frm.doc);
		});
		this.frm.set_query("customer_address", function() {
			frappe.dynamic_link = { doc: me.frm.doc, fieldname: 'customer', doctype: 'Customer' };
			return erpnext.queries.address_query(me.frm.doc);
		});

		this.frm.set_query("source_warehouse_address", () => {
			frappe.dynamic_link = { doc: me.frm.doc, fieldname: 'from_warehouse', doctype: 'Warehouse' };
			return erpnext.queries.address_query(me.frm.doc);
		});
		this.frm.set_query("target_warehouse_address", () => {
			frappe.dynamic_link = { doc: me.frm.doc, fieldname: 'to_warehouse', doctype: 'Warehouse' };
			return erpnext.queries.address_query(me.frm.doc);
		});

		let batch_no_field = this.frm.get_docfield("items", "batch_no");
		if (batch_no_field) {
			batch_no_field.get_route_options_for_new_doc = function(row) {
				return {
					"item": row.doc.item_code
				}
			};
		}
	}

	onload() {
		erpnext.utils.setup_scan_barcode_field(this.frm.fields_dict.scan_barcode);
	}

	onload_post_render() {
		var me = this;
		this.set_default_account(false, function() {
			if(me.frm.doc.__islocal && me.frm.doc.company && !me.frm.doc.amended_from) {
				me.frm.trigger("company");
			}
		});

		this.frm.get_field("items").grid.set_multiple_add("item_code", "qty");
	}

	refresh() {
		erpnext.toggle_naming_series();
		this.toggle_related_fields(this.frm.doc);
		this.toggle_enable_bom();
		this.show_stock_ledger();
		this.setup_buttons();

		if (this.frm.doc.company) {
			this.frm.trigger("toggle_display_account_head");
		}

		this.frm.trigger("setup_quality_inspection");

		if (this.frm.doc.docstatus===1 && erpnext.is_perpetual_inventory_enabled(this.frm.doc.company)) {
			this.show_general_ledger();
		}
		erpnext.hide_company();
		erpnext.utils.add_item(this.frm);

		if (this.frm.doc.docstatus === 0) {
			this.create_select_batch_button();
		}

		this.frm.set_indicator_formatter('item_code', function(doc, parent) {
			if (!doc.s_warehouse) {
				return 'blue';
			} else {
				if (doc.docstatus === 0) {
					if (!doc.actual_qty) {
						return "red";
					} else if (doc.actual_qty < doc.stock_qty) {
						return "orange";
					} else {
						return "green";
					}
				} else {
					if (parent.purpose === "Send to Warehouse") {
						if (!doc.transferred_qty) {
							return "orange";
						} else if (doc.transferred_qty < doc.qty) {
							return "yellow";
						} else {
							return "green";
						}
					} else {
						return "green";
					}
				}
			}
		});
	}

	setup_buttons() {
		let me = this;

		const has_alternative_items = (me.frm.doc.items || []).find(d => d.has_alternative_item);
		if (me.frm.doc.docstatus == 0 && has_alternative_items) {
			me.frm.add_custom_button(__('Alternate Item'), () => {
				erpnext.utils.select_alternate_items({
					frm: me.frm,
					child_docname: "items",
					warehouse_field: "s_warehouse",
					child_doctype: "Stock Entry Detail",
					original_item_field: "original_item",
					condition: (d) => d.s_warehouse && d.has_alternative_item,
				})
			});
		}

		if (me.frm.doc.docstatus === 1 && me.frm.doc.purpose == 'Send to Warehouse') {
			if (me.frm.doc.per_transferred < 100) {
				me.frm.add_custom_button(__('Receive at Warehouse Entry'), function() {
					frappe.model.open_mapped_doc({
						method: "erpnext.stock.doctype.stock_entry.stock_entry.make_stock_in_entry",
						frm: me.frm
					})
				});
			}

			if (me.frm.doc.per_transferred > 0) {
				me.frm.add_custom_button(__('Received Stock Entries'), function() {
					frappe.route_options = {
						'outgoing_stock_entry': me.frm.doc.name,
						'docstatus': ['<', 2]
					};

					frappe.set_route('List', 'Stock Entry');
				}, __("View"));
			}
		}

		if (me.frm.doc.docstatus===0) {
			if (frappe.model.can_read("Material Request")) {
				me.frm.add_custom_button(__('Material Request'), function () {
					erpnext.utils.map_current_doc({
						method: "erpnext.stock.doctype.material_request.material_request.make_stock_entry",
						source_doctype: "Material Request",
						target: me.frm,
						date_field: "transaction_date",
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
							material_request_type: ["in", ["Material Transfer", "Material Issue"]],
							order_status: "To Order",
							status: ["!=", "Stopped"]
						}
					})
				}, __("Get Items From"));
			}

			if (frappe.model.can_read("Packing Slip")) {
				me.frm.add_custom_button(__('Packing Slip'), () => {
					me.get_items_from_packing_slip("Stock Entry");
				}, __("Get Items From"));
			}
		}

		if (me.frm.doc.docstatus===0 && me.frm.doc.purpose == "Material Issue") {
			me.frm.add_custom_button(__('Expired Batches'), function() {
				frappe.call({
					method: "erpnext.stock.doctype.stock_entry.stock_entry.get_expired_batch_items",
					callback: function(r) {
						if (!r.exc && r.message) {
							me.frm.set_value("items", []);
							r.message.forEach(function(element) {
								let d = me.frm.add_child("items");
								d.item_code = element.item;
								d.s_warehouse = element.warehouse;
								d.qty = element.qty;
								d.uom = element.stock_uom;
								d.conversion_factor = 1;
								d.batch_no = element.batch_no;
								d.stock_qty = element.qty;
								me.frm.refresh_fields();
							});
						}
					}
				});
			}, __("Get Items From"));
		}

		if(me.frm.doc.docstatus==1 && me.frm.doc.purpose == "Material Receipt" && me.frm.get_sum('items', 'sample_quantity')) {
			me.frm.add_custom_button(__('Create Sample Retention Stock Entry'), function () {
				me.frm.trigger("make_retention_stock_entry");
			});
		}

		me.add_get_applicable_items_button();
		me.add_get_project_template_items_button();
	}

	create_select_batch_button(doc, cdt, cdn) {
		let me = this;

		me.frm.fields_dict.items.grid.add_custom_button(__("Select Batches"), function() {
			if (me.frm.focused_item_dn) {
				var row = frappe.get_doc("Stock Entry Detail", me.frm.focused_item_dn);
				erpnext.stock.select_batch_and_serial_no(me.frm, row);
			}
		});
		me.frm.fields_dict.items.grid.custom_buttons[__("Select Batches")].addClass('hidden btn-primary');
	}

	items_row_focused(doc, cdt, cdn) {
		var row = frappe.get_doc(cdt, cdn);
		this.frm.focused_item_dn = row ? row.name : null;
		this.show_hide_select_batch_button();
	}

	show_hide_select_batch_button() {
		var row;
		if (this.frm.focused_item_dn) {
			row = frappe.get_doc(this.frm.doc.doctype + " Detail", this.frm.focused_item_dn);
		}

		var show_select_batch = row
			&& row.item_code
			&& row.has_batch_no
			&& row.s_warehouse
			&& this.frm.doc.docstatus === 0
			&& this.frm.doc.purpose !== 'Material Receipt'

		var button = this.frm.fields_dict.items.grid.custom_buttons[__("Select Batches")];
		if (button) {
			if (show_select_batch) {
				button.removeClass('hidden');
			} else {
				button.addClass('hidden');
			}
		}
	}

	get_args_for_stock_entry_type() {
		var items = [];
		$.each(this.frm.doc.items || [], function (i, d){
			items.push({"name": d.name, "expense_account": d.expense_account})
		});
		var args = {
			stock_entry_type: this.frm.doc.stock_entry_type,
			company: this.frm.doc.company,
			is_opening: this.frm.doc.is_opening,
			items: items
		}
		return args;
	}

	stock_entry_type() {
		var me = this;
		if (me.frm.doc.stock_entry_type) {
			var args = me.get_args_for_stock_entry_type()
			frappe.call({
				method: "erpnext.stock.doctype.stock_entry_type.stock_entry_type.get_stock_entry_type_details",
				args: {
					args: args
				},
				callback: function (r) {
					if (!r.exc) {
						me.frm.set_value(r.message.parent);
						if (r.message.items) {
							$.each(me.frm.doc.items || [], function (i, d) {
								if (r.message.items[d.name]) {
									d.expense_account = r.message.items[d.name];
									refresh_field("expense_account", d.name, "items");
								}
							})
						}
						me.toggle_related_fields(me.frm.doc);
					}
				}
			});
		}
	}

	get_warehouse_filters(fieldname, filters) {
		if (this.frm.doc.source_warehouse_type && ['s_warehouse', 'from_warehouse'].includes(fieldname)) {
			filters.push(['Warehouse', 'warehouse_type', '=', this.frm.doc.source_warehouse_type]);
		}
		if (this.frm.doc.target_warehouse_type && ['t_warehouse', 'to_warehouse'].includes(fieldname)) {
			filters.push(['Warehouse', 'warehouse_type', '=', this.frm.doc.target_warehouse_type]);
		}
	}

	scan_barcode() {
		const barcode_scanner = new erpnext.utils.BarcodeScanner({
			frm: this.frm,
			prompt_qty: true,
		});
		barcode_scanner.process_scan();
	}

	on_submit() {
		this.clean_up();
	}

	after_cancel() {
		this.clean_up();
	}

	set_default_account(force, callback) {
		var me = this;
		var args = me.get_args_for_stock_entry_type()

		if(this.frm.doc.company && erpnext.is_perpetual_inventory_enabled(this.frm.doc.company)) {
			return this.frm.call({
				method: "erpnext.stock.doctype.stock_entry.stock_entry.get_item_expense_accounts",
				args: {
					args: args
				},
				callback: function(r) {
					if (!r.exc) {
						$.each(me.frm.doc.items || [], function (i, d) {
							if (r.message[d.name]) {
								if(!d.expense_account || force) {
									d.expense_account = r.message[d.name];
									refresh_field("expense_account", d.name, "items");
								}
							}
						})
						if(callback) callback();
					}
				}
			});
		}
	}

	is_opening() {
		this.set_default_account(true);
	}

	clean_up() {
		// Clear Work Order record from locals, because it is updated via Stock Entry
		if(this.frm.doc.work_order &&
			in_list(["Manufacture", "Material Transfer for Manufacture", "Material Consumption for Manufacture"],
				this.frm.doc.purpose)) {
			frappe.model.remove_from_locals("Work Order",
				this.frm.doc.work_order);
		}
	}

	get_items() {
		var me = this;
		if(!this.frm.doc.fg_completed_qty || !this.frm.doc.bom_no)
			frappe.throw(__("BOM and Manufacturing Quantity are required"));

		if(this.frm.doc.work_order || this.frm.doc.bom_no) {
			// if work order / bom is mentioned, get items
			return this.frm.call({
				doc: me.frm.doc,
				method: "get_items",
				callback: function(r) {
					if(!r.exc){
						refresh_field("items");
						me.frm.dirty();
					}
				}
			});
		}
	}

	work_order() {
		var me = this;
		this.toggle_enable_bom();
		if(!me.frm.doc.work_order || me.frm.doc.job_card) {
			return;
		}

		return frappe.call({
			method: "erpnext.stock.doctype.stock_entry.stock_entry.get_work_order_details",
			args: {
				work_order: me.frm.doc.work_order,
				purpose: me.frm.doc.purpose,
			},
			callback: function(r) {
				if (!r.exc) {
					$.each(["from_bom", "bom_no", "fg_completed_qty", "use_multi_level_bom"], function(i, field) {
						me.frm.set_value(field, r.message[field]);
					})

					if (me.frm.doc.purpose == "Material Transfer for Manufacture" && !me.frm.doc.to_warehouse)
						me.frm.set_value("to_warehouse", r.message["wip_warehouse"]);


					if (me.frm.doc.purpose == "Manufacture" || me.frm.doc.purpose == "Material Consumption for Manufacture" ) {
						if (me.frm.doc.purpose == "Manufacture") {
							if (!me.frm.doc.to_warehouse) me.frm.set_value("to_warehouse", r.message["fg_warehouse"]);
						}
						if (!me.frm.doc.from_warehouse) me.frm.set_value("from_warehouse", r.message["wip_warehouse"]);
					}
					me.get_items();
				}
			}
		});
	}

	toggle_enable_bom() {
		this.frm.toggle_enable("bom_no", !!!this.frm.doc.work_order);
	}

	add_excise_button() {
		if(frappe.boot.sysdefaults.country === "India")
			this.frm.add_custom_button(__("Excise Invoice"), function() {
				var excise = frappe.model.make_new_doc_and_get_name('Journal Entry');
				excise = locals['Journal Entry'][excise];
				excise.voucher_type = 'Excise Entry';
				frappe.set_route('Form', 'Journal Entry', excise.name);
			}, __('Create'));
	}

	items_add(doc, cdt, cdn) {
		var row = frappe.get_doc(cdt, cdn);
		this.frm.script_manager.copy_from_first_row("items", row, ["expense_account", "cost_center"]);

		if(!row.s_warehouse) row.s_warehouse = this.frm.doc.from_warehouse;
		if(!row.t_warehouse) row.t_warehouse = this.frm.doc.to_warehouse;
	}

	from_warehouse(doc) {
		this.set_warehouse_in_children(doc.items, "s_warehouse", doc.from_warehouse);
		this.get_warehouse_address("from_warehouse", "source_warehouse_address");
	}

	to_warehouse(doc) {
		this.set_warehouse_in_children(doc.items, "t_warehouse", doc.to_warehouse);
		this.get_warehouse_address("to_warehouse", "target_warehouse_address");
	}

	get_warehouse_address(warehouse_field, address_field) {
		let warehouse = this.frm.doc[warehouse_field];
		if (!warehouse) {
			return;
		}

		return frappe.call({
			method: "frappe.contacts.doctype.address.address.get_default_address",
			args: {
				doctype: "Warehouse",
				name: warehouse
			},
			callback: (r) => {
				this.frm.set_value(address_field, r.message);
			}
		});
	}

	set_warehouse_in_children(child_table, warehouse_field, warehouse) {
		erpnext.utils.autofill_warehouse(child_table, warehouse_field, warehouse);
	}

	items_on_form_rendered(doc, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		erpnext.setup_serial_no(() => {
			erpnext.stock.select_batch_and_serial_no(this.frm, row);
		});
	}

	toggle_related_fields(doc) {
		this.frm.toggle_enable("from_warehouse", doc.purpose!='Material Receipt');
		this.frm.toggle_enable("to_warehouse", doc.purpose!='Material Issue');

		this.frm.fields_dict["items"].grid.toggle_enable("s_warehouse", doc.purpose !== 'Material Receipt');
		this.frm.fields_dict["items"].grid.toggle_enable("t_warehouse", doc.purpose !== 'Material Issue');

		if (doc.purpose == 'Material Receipt') {
			doc.from_warehouse = null;
			refresh_field('from_warehouse');
		}
		if (doc.purpose == 'Material Issue') {
			doc.to_warehouse = null;
			refresh_field('to_warehouse');
		}

		this.frm.fields_dict["items"].grid.set_column_disp("retain_sample", doc.purpose=='Material Receipt');
		this.frm.fields_dict["items"].grid.set_column_disp("sample_quantity", doc.purpose=='Material Receipt');

		this.frm.cscript.toggle_enable_bom();

		var customer_provided = in_list(['Material Receipt', 'Material Issue'], doc.purpose) && cint(doc.customer_provided);
		this.frm.toggle_reqd("customer", customer_provided);

		if (doc.purpose == 'Send to Subcontractor') {
			doc.customer = doc.customer_name = doc.customer_address =
				doc.delivery_note_no = doc.sales_invoice_no = null;
		} else {
			if (!customer_provided) {
				doc.customer = doc.customer_name = doc.customer_address =
					doc.delivery_note_no = doc.sales_invoice_no = null;
			}
			doc.supplier = doc.supplier_name = doc.supplier_address =
				doc.purchase_receipt_no = doc.address_display = null;
		}

		if (!doc.customer_address && !doc.supplier_address) {
			doc.address_display = null;
		}

		if(doc.purpose == "Material Receipt") {
			this.frm.set_value("from_bom", 0);
		}

		// Addition costs based on purpose
		this.frm.toggle_display(["additional_costs", "total_additional_costs", "additional_costs_section"],
			doc.purpose!='Material Issue');

		this.frm.fields_dict["items"].grid.set_column_disp("additional_cost", doc.purpose!='Material Issue');
		this.frm.toggle_reqd("outgoing_stock_entry",
			doc.purpose == 'Receive at Warehouse' ? 1: 0);
	}

	supplier(doc) {
		return erpnext.utils.get_party_details(this.frm, null, null, null);
	}

	customer(doc) {
		return erpnext.utils.get_party_details(this.frm, null, null, null);
	}

	set_item_cost_centers(row) {
		return this.frm.call({
			doc: this.frm.doc,
			method: "set_item_cost_centers",
			args: {
				row: row
			},
			callback: function(r) {
				if (!r.exc) {
					refresh_field("items");
				}
			}
		});
	}
};

erpnext.stock.select_batch_and_serial_no = (frm, item) => {
	let get_warehouse_type_and_name = (item) => {
		let value = '';
		if(frm.fields_dict.from_warehouse.disp_status === "Write") {
			value = cstr(item.s_warehouse) || '';
			return {
				type: 'Source Warehouse',
				name: value
			};
		} else {
			value = cstr(item.t_warehouse) || '';
			return {
				type: 'Target Warehouse',
				name: value
			};
		}
	}

	if (frm.doc.purpose === 'Material Receipt') return;

	if (!item || !item.item_code || (!item.has_batch_no && !item.has_serial_no)) {
		return;
	}

	let warehouse_details = get_warehouse_type_and_name(item);
	let warehouse_field;
	if (warehouse_details.type == "Source Warehouse") {
		warehouse_field = "s_warehouse";
	} else if (warehouse_details.type == "Target Warehouse") {
		warehouse_field = "t_warehouse";
	}

	new erpnext.stock.SerialBatchSelector(frm, item, {
		warehouse_field: warehouse_field,
	});
}

extend_cscript(cur_frm.cscript, new erpnext.stock.StockEntry({frm: cur_frm}));
