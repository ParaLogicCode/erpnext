// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

// eslint-disable-next-line
{% include 'erpnext/public/js/controllers/buying.js' %};

erpnext.buying.MaterialRequestController = class MaterialRequestController extends erpnext.buying.BuyingController {
	setup() {
		this.frm.custom_make_buttons = {
			'Stock Entry': 'Transfer Material',
			'Pick List': 'Pick List',
			'Purchase Order': 'Purchase Order',
			'Request for Quotation': 'Request for Quotation',
			'Supplier Quotation': 'Supplier Quotation',
			'Work Order': 'Work Order',
			'Material Request': 'Procurement Request',
		};

		erpnext.setup_applies_to_fields(this.frm);

		erpnext.utils.setup_projected_qty_formatter("Material Request Item", "actual_qty");
		erpnext.utils.setup_projected_qty_formatter("Material Request Item", "projected_qty");

		this.setup_queries();
	}

	refresh() {
		erpnext.hide_company(this.frm);
		this.setup_buttons();

		// formatter for material request item
		this.frm.set_indicator_formatter('item_code', (doc) => {
			if (this.frm.doc.docstatus == 1) {
				let completed_qty = Math.max(doc.ordered_qty, doc.received_qty);
				if (!completed_qty) {
					return "orange";
				} else if (completed_qty < doc.stock_qty) {
					return "yellow";
				} else {
					return "green";
				}
			}
		});
	}

	onload() {
		erpnext.utils.add_item(this.frm);
		set_schedule_date(this.frm);
	}

	onload_post_render() {
		this.frm.get_field("items").grid.set_multiple_add("item_code", "qty");
	}

	setup_queries() {
		erpnext.queries.setup_queries(this.frm, "Warehouse", () => {
			return erpnext.queries.warehouse(this.frm.doc);
		});

		this.frm.set_query("item_code", "items", (doc) => {
			if (doc.material_request_type == "Customer Provided") {
				return erpnext.queries.item({customer: this.frm.doc.customer});
			} else if (doc.material_request_type != "Manufacture") {
				return erpnext.queries.item({is_purchase_item: 1});
			}
		});

		this.frm.set_query("uom", "items", function(doc, cdt, cdn) {
			let row = locals[cdt][cdn];
			return {
				query : "erpnext.controllers.queries.item_uom_query",
				filters: {
					item_code: row.item_code
				}
			}
		});
	}

	setup_buttons() {
		if (this.frm.doc.docstatus == 0) {
			if (frappe.model.can_read("Sales Order")) {
				this.frm.add_custom_button(__('Sales Order'), () => this.get_items_from_sales_order(),
					__("Get Items From"));
			}

			if (frappe.model.can_read("BOM")) {
				this.frm.add_custom_button(__("Bill of Materials"), () => this.get_items_from_bom(),
					__("Get Items From"));
			}

			this.set_from_product_bundle();

			this.add_get_applicable_items_button("stock");
			this.add_get_project_template_items_button("stock");

			erpnext.utils.setup_remove_zero_qty_rows(this.frm);
			this.frm.fields_dict.items.grid.add_custom_button(__("Round Up Qty"), () => this.round_up_qty());
		}

		if (this.frm.doc.docstatus == 1 && this.frm.doc.order_status == "To Order" && this.frm.has_perm("write")) {
			if (this.frm.doc.status != 'Stopped') {
				this.frm.add_custom_button(__('Stop'), () => this.update_status('Stopped'));
			} else {
				this.frm.add_custom_button(__('Re-Open'), () => this.update_status('Submitted'));
			}
		}

		if (this.frm.doc.docstatus == 1 && this.frm.doc.status != 'Stopped') {
			if (this.frm.doc.order_status == "To Order") {
				const add_create_pick_list_button = () => {
					if (frappe.model.can_create("Pick List")) {
						this.frm.add_custom_button(__('Pick List'), () => this.create_pick_list(),
							__('Create'));
					}
				}

				if (this.frm.doc.material_request_type === "Material Transfer") {
					if (frappe.model.can_create("Stock Entry")) {
						this.frm.add_custom_button(__("Transfer Material"), () => this.make_stock_entry(),
							__('Create'));
					}
					add_create_pick_list_button();
				}

				if (this.frm.doc.material_request_type === "Material Issue") {
					if (frappe.model.can_create("Stock Entry")) {
						this.frm.add_custom_button(__("Issue Material"), () => this.make_stock_entry(),
							__('Create'));
					}

					add_create_pick_list_button();

					if (frappe.model.can_create("Material Request")) {
						this.frm.add_custom_button(__("Procurement Request"), () => this.make_procurement_request(),
							__('Create'));
					}
				}

				if (this.frm.doc.material_request_type === "Customer Provided") {
					if (frappe.model.can_create("Stock Entry")) {
						this.frm.add_custom_button(__("Material Receipt"), () => this.make_stock_entry(),
							__('Create'));
					}
				}

				if (this.frm.doc.material_request_type === "Purchase") {
					if (frappe.model.can_create("Purchase Order")) {
						this.frm.add_custom_button(__('Purchase Order'), () => this.make_purchase_order(),
							__('Create'));
					}

					if (frappe.model.can_create("Request for Quotation")) {
						this.frm.add_custom_button(__("Request for Quotation"), () => this.make_request_for_quotation(),
							__('Create'));
					}

					if (frappe.model.can_create("Supplier Quotation")) {
						this.frm.add_custom_button(__("Supplier Quotation"), () => this.make_supplier_quotation(),
							__('Create'));
					}
				}

				if (this.frm.doc.material_request_type === "Manufacture") {
					if (frappe.model.can_create("Work Order")) {
						this.frm.add_custom_button(__("Work Order"), () => this.raise_work_orders(),
							__('Create'));
					}
				}

				this.frm.page.set_inner_btn_group_as_primary(__('Create'));
			}
		}
	}

	schedule_date(doc, cdt, cdn) {
		if (cdt && cdn) {
			let row = locals[cdt][cdn];
			if (row.schedule_date) {
				if(!this.frm.doc.schedule_date) {
					erpnext.utils.copy_value_in_all_rows(this.frm.doc, cdt, cdn, "items", "schedule_date");
				} else {
					set_schedule_date(this.frm);
				}
			}
		} else {
			set_schedule_date(this.frm);
		}
	}

	set_warehouse() {
		erpnext.utils.autofill_warehouse(this.frm.doc.items, "warehouse", this.frm.doc.set_warehouse);
	}

	project() {
		this.get_project_details();
	}

	tc_name() {
		this.get_terms();
	}

	item_code(doc, cdt, cdn) {
		let row = locals[cdt][cdn];
		row.rate = 0;
		this.get_item_data(row);
	}

	qty(doc, cdt, cdn) {
		this.calculate_totals();
	}

	rate(doc, cdt, cdn) {
		this.calculate_totals();
	}

	items_remove() {
		this.calculate_totals();
	}

	validate_company_and_party() {
		return true;
	}

	calculate_taxes_and_totals() { }

	round_up_qty() {
		if (this.frm.doc.docstatus === 0) {
			this.frm.call({
				method: "round_up_qty",
				doc: this.frm.doc,
				callback: (r) => {
					this.frm.dirty();
				}
			});
		}
	}

	items_add(doc, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		// var item = frappe.get_doc(cdt, cdn);
		if(!row.warehouse && doc.set_warehouse) {
			row.warehouse = doc.set_warehouse;
			refresh_field("warehouse", cdn, "items");
		} if(doc.schedule_date) {
			row.schedule_date = doc.schedule_date;
			refresh_field("schedule_date", cdn, "items");
		} else {
			this.frm.script_manager.copy_from_first_row("items", row, ["schedule_date"]);
		}
	}

	calculate_totals() {
		this.frm.doc.total_qty = 0;
		this.frm.doc.total_alt_uom_qty = 0;

		for (let d of this.frm.doc.items || []) {
			frappe.model.round_floats_in(d);

			d.stock_qty = flt(d.qty * d.conversion_factor, 6);
			d.alt_uom_size = d.alt_uom ? d.alt_uom_size : 1.0
			d.alt_uom_qty = flt(d.stock_qty * d.alt_uom_size, precision("alt_uom_qty", d));

			d.amount = flt(d.rate * d.qty, precision("amount", d));

			this.frm.doc.total_qty += d.qty;
			this.frm.doc.total_alt_uom_qty += d.alt_uom_qty;
		}

		frappe.model.round_floats_in(this.frm.doc, [
			'total_qty', 'total_alt_uom_qty',
		]);

		this.frm.refresh_fields();
	}

	get_items_from_sales_order() {
		erpnext.utils.map_current_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_material_request",
			source_doctype: "Sales Order",
			target: this.frm,
			setters: {
				company: this.frm.doc.company
			},
			get_query_filters: {
				docstatus: 1,
				status: ["not in", ["Closed", "On Hold"]],
				delivery_status: "To Deliver",
			}
		});
	}

	get_items_from_bom() {
		let dialog = new frappe.ui.Dialog({
			title: __("Get Items from BOM"),
			fields: [
				{"fieldname":"bom", "fieldtype":"Link", "label":__("BOM"),
					options:"BOM", reqd: 1, get_query: () => {
						return {filters: { docstatus:1 }};
					}},
				{"fieldname":"warehouse", "fieldtype":"Link", "label":__("Warehouse"),
					options:"Warehouse"},
				{"fieldname":"qty", "fieldtype":"Float", "label":__("Quantity"),
					reqd: 1, "default": 1},
				{"fieldname":"fetch_exploded", "fieldtype":"Check",
					"label":__("Fetch exploded BOM (including sub-assemblies)"), "default":1},
				{fieldname:"fetch", "label":__("Get Items from BOM"), "fieldtype":"Button"}
			]
		});

		dialog.get_input("fetch").on("click", () => {
			let values = dialog.get_values();
			if (!values) {
				return;
			}

			if (!this.frm.doc.company) {
				frappe.throw(__("Company field is required"));
			}
			values["company"] = this.frm.doc.company;

			this.frm.call({
				method: "get_bom_items",
				doc: this.frm.doc,
				args: values,
				callback: (r) => {
					if (!r.exc) {
						dialog.hide();
						set_schedule_date(this.frm);
						refresh_field("items");
					}
				}
			});
		});
		dialog.show();
	}

	update_status(status) {
		return frappe.call({
			method: "erpnext.stock.doctype.material_request.material_request.update_status",
			args: {
				name: this.frm.doc.name,
				status: status
			},
			callback: (r) => {
				if (!r.exc) {
					this.frm.reload_doc();
				}
			}
		});
	}

	get_item_data(item) {
		if (item && !item.item_code) {
			return;
		}

		return this.frm.call({
			method: "erpnext.stock.get_item_details.get_item_details",
			child: item,
			args: {
				args: {
					doctype: this.frm.doc.doctype,
					item_code: item.item_code,
					warehouse: item.warehouse,
					set_warehouse: this.frm.doc.set_warehouse,
					buying_price_list: frappe.defaults.get_default('buying_price_list'),
					currency: frappe.defaults.get_default('Currency'),
					name: this.frm.doc.name,
					transaction_date: this.frm.doc.transaction_date,
					qty: item.qty || 1,
					uom: item.uom,
					stock_qty: item.stock_qty,
					company: this.frm.doc.company,
					conversion_rate: 1,
					material_request_type: this.frm.doc.material_request_type,
					plc_conversion_rate: 1,
					rate: item.rate,
					conversion_factor: item.conversion_factor,
					child_docname: item.name,
				}
			},
			callback: (r) => {
				this.calculate_totals();
			}
		});
	}

	make_purchase_order() {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_purchase_order",
			frm: this.frm,
		});
	}

	make_request_for_quotation() {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_request_for_quotation",
			frm: this.frm,
			run_link_triggers: true
		});
	}

	make_supplier_quotation() {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_supplier_quotation",
			frm: this.frm
		});
	}

	make_stock_entry() {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_stock_entry",
			frm: this.frm
		});
	}

	make_procurement_request() {
		frappe.prompt([{
			label: __("Request Type"),
			fieldname: "material_request_type",
			fieldtype: "Select",
			options: ["", "Purchase", "Material Transfer"],
			reqd: 1,
		}], (values) => {
			if (values.material_request_type == "Purchase") {
				this.make_purchase_request();
			} else if (values.material_request_type == "Material Transfer") {
				this.make_transfer_request();
			}
		}, __("Select Request Type"))
	}

	make_purchase_request() {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_purchase_request",
			frm: this.frm
		})
	}

	make_transfer_request() {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.make_transfer_request",
			frm: this.frm
		})
	}

	create_pick_list() {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.material_request.material_request.create_pick_list",
			frm: this.frm
		});
	}

	raise_work_orders() {
		return frappe.call({
			method:"erpnext.stock.doctype.material_request.material_request.raise_work_orders",
			args: {
				"material_request": this.frm.doc.name
			},
			callback: (r) => {
				if (r.message.length) {
					this.frm.reload_doc();
				}
			}
		});
	}
};

// for backward compatibility: combine new and previous states
extend_cscript(cur_frm.cscript, new erpnext.buying.MaterialRequestController({frm: cur_frm}));

function set_schedule_date(frm) {
	if (frm.doc.schedule_date) {
		$.each(frm.doc.items || [], (i, d) => {
			d.schedule_date = frm.doc.schedule_date;
		});
		refresh_field("items");
	}
}
