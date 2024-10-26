// Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.provide("erpnext.manufacturing");

erpnext.manufacturing.WorkOrderController = class WorkOrderController extends frappe.ui.form.Controller {
	setup() {
		this.frm.doc.disable_item_formatter = true;

		this.frm.custom_make_buttons = {
			"Stock Entry": "Start",
			"Pick List": "Pick List",
			"Packing Slip": "Packing Slip",
			"Job Card": "Create Job Card",
			"Work Order": "Sub-Assembly Work Order",
			"Purchase Order": "Subcontract Order",
			"Purchase Receipt": "Subcontract Receipt",
		};

		this.setup_queries();
	}

	refresh() {
		this.frm.doc.disable_item_formatter = true;

		erpnext.toggle_naming_series();
		erpnext.hide_company();
		this.setup_buttons();
		this.setup_progressbars();

		// formatter for work order operation
		this.frm.set_indicator_formatter("operation", (row) => {
			return flt(row.completed_qty) < flt(this.frm.doc.qty) ? "orange" : "green";
		});

		if (this.frm.is_new()) {
			this.set_default_warehouse();
		}
	}

	onload() {
		this.frm.add_fetch("sales_order", "project", "project");
	}

	before_submit() {
		this.frm.toggle_reqd("transfer_material_against", this.frm.doc.operations && this.frm.doc.operations.length);
	}

	setup_queries() {
		this.frm.set_query("production_item", () => {
			return {
				query: "erpnext.controllers.queries.item_query",
				filters: {
					"is_stock_item": 1,
					"default_bom": ["is", "set"],
				}
			};
		});

		this.frm.set_query("bom_no", () => {
			if (this.frm.doc.production_item) {
				return {
					query: "erpnext.controllers.queries.bom",
					filters: {item: cstr(this.frm.doc.production_item)}
				};
			} else {
				frappe.throw(__("Please enter Production Item first"));
			}
		});

		this.frm.set_query("sales_order", () => {
			return {
				filters: {
					"docstatus": 1,
					"status": ["not in", ["Closed", "On Hold"]]
				}
			};
		});

		this.frm.set_query("project", () => {
			return{
				filters: {
					"status": ['not in', ['Completed', 'Cancelled']]
				}
			};
		});

		this.frm.set_query("operation", "required_items", () => {
			return {
				query: "erpnext.manufacturing.doctype.work_order.work_order.get_bom_operations",
				filters: {
					"parent": this.frm.doc.bom_no,
					"parenttype": "BOM"
				}
			};
		});

		this.frm.set_query("workstation", "operations", (doc, cdt, cdn) => {
			let row = frappe.get_doc(cdt, cdn);
			return erpnext.queries.workstation(row.operation);
		});

		// Warehouse Queries
		this.frm.set_query("source_warehouse", () => {
			return {
				filters: {
					"company": this.frm.doc.company,
				}
			};
		});

		this.frm.set_query("source_warehouse", "required_items", () => {
			return {
				filters: {
					"company": this.frm.doc.company,
				}
			};
		});

		this.frm.set_query("batch_no", "required_items", (doc, cdt, cdn) => {
			let row = frappe.get_doc(cdt, cdn);

			if (!row.item_code) {
				frappe.throw(__("Please select Item to get Batch Number"));
			} else {
				if (row.source_warehouse) {
					return {
						query: "erpnext.controllers.queries.get_batch_no",
						filters: {
							item_code: row.item_code,
							warehouse: row.source_warehouse,
							posting_date: frappe.datetime.nowdate(),
						}
					};
				} else {
					return {
						filters: {
							item: row.item_code,
						}
					}
				}
			}
		});

		this.frm.set_query("wip_warehouse", () => {
			return {
				filters: {
					"company": this.frm.doc.company,
					"is_group": 0,
				}
			};
		});

		this.frm.set_query("fg_warehouse", () => {
			return {
				filters: {
					"company": this.frm.doc.company,
					"is_group": 0
				}
			};
		});

		this.frm.set_query("scrap_warehouse", () => {
			return {
				filters: {
					"company": this.frm.doc.company,
					"is_group": 0
				}
			};
		});
	}

	setup_buttons() {
		let doc = this.frm.doc;

		// Start / Finish
		this.setup_start_finish_buttons();

		// Stop / Resume
		if (doc.docstatus === 1) {
			if (doc.status != 'Stopped' && doc.status != 'Completed') {
				this.frm.add_custom_button(__("Stop"), () => {
					this.stop_work_order("Stopped");
				}, __("Status"));
			} else if (doc.status == 'Stopped') {
				this.frm.add_custom_button(__("Re-Open"), () => {
					this.stop_work_order("Resumed");
				}, __("Status"));
			}
		}

		// Create Job Card
		if (
			doc.docstatus === 1
			&& doc.operations && doc.operations.length
			&& flt(doc.completed_qty) < flt(doc.qty)
			&& !cint(frappe.defaults.get_default("disable_capacity_planning"))
		) {
			const not_completed = doc.operations.some(d => d.status != "Completed");
			if (not_completed) {
				this.frm.add_custom_button(__('Create Job Card'), () => {
					this.create_job_cards();
				}).addClass('btn-primary');
			}
		}

		// Alternate Item
		const has_alternative_items = (doc.required_items || []).find(d => d.has_alternative_item);
		if (doc.docstatus == 0 && has_alternative_items) {
			this.frm.add_custom_button(__('Alternate Item'), () => {
				erpnext.utils.select_alternate_items({
					frm: this.frm,
					child_docname: "required_items",
					warehouse_field: "source_warehouse",
					child_doctype: "Work Order Item",
					original_item_field: "original_item",
					condition: (d) => d.has_alternative_item,
				});
			});
		}

		if (doc.docstatus == 1 && doc.status != "Stopped") {
			// Packing Slip
			let packing_completion = flt(doc.packed_qty) + flt(doc.rejected_qty) + flt(doc.reconciled_qty);

			if (
				frappe.model.can_create("Packing Slip")
				&& doc.packing_slip_required
				&& flt(packing_completion, precision("qty")) < flt(doc.completed_qty, precision("qty"))
			) {
				this.frm.add_custom_button(__("Packing Slip"), () => {
					this.make_packing_slip();
				}, __("Create"));
			}

			// Sub Assembly Work Orders
			if (!this.frm.doc.use_multi_level_bom && doc.status != "Completed" && frappe.model.can_create("Work Order")) {
				this.frm.add_custom_button(__("Sub-Assembly Work Order"), () => this.make_sub_assembly_work_orders(),
					__("Create"));
			}

			// Subcontract Order
			let subcontractable_qty = erpnext.manufacturing.get_subcontractable_qty(doc);
			if (subcontractable_qty > 0 && doc.status != "Completed" && frappe.model.can_create("Purchase Order")) {
				this.frm.add_custom_button(__("Subcontract Order"), () => {
					this.make_purchase_order();
				}, __("Create"));
			}

			// Pick List
			if (this.frm.show_pick_list_btn) {
				this.frm.add_custom_button(__("Pick List"), () => {
					this.make_pick_list("Material Transfer for Manufacture");
				}, __("Create"));
			}

			// New BOM button
			// if (
			// 	doc.status == "Completed"
			// 	&& doc.__onload.backflush_raw_materials_based_on == "Material Transferred for Manufacture"
			// ) {
			// 	this.frm.add_custom_button(__("BOM"), () => {
			// 		this.make_bom();
			// 	}, __("Create"));
			// }
		}
	}

	setup_start_finish_buttons() {
		let doc = this.frm.doc;
		if (doc.docstatus != 1 || doc.status == "Stopped") {
			return;
		}

		let qty_with_allowance = erpnext.manufacturing.get_qty_with_allowance(doc.producible_qty, doc);

		// Start Button
		const show_start_btn = (
			erpnext.manufacturing.has_stock_entry_permission()
			&& !doc.skip_transfer
			&& doc.transfer_material_against != "Job Card"
			&& flt(doc.material_transferred_for_manufacturing) < qty_with_allowance
		);

		let start_btn_default = (
			(
				!flt(doc.material_transferred_for_manufacturing) ||
				flt(doc.material_transferred_for_manufacturing, precision("qty")) < flt(doc.producible_qty, precision("qty"))
			) &&
			flt(doc.produced_qty, precision("qty")) < flt(doc.producible_qty, precision("qty"))
		)

		if (show_start_btn) {
			this.frm.show_pick_list_btn = frappe.model.can_create("Pick List");

			let start_btn = this.frm.add_custom_button(__("Start"), () => {
				erpnext.manufacturing.start_work_order(doc);
			});

			if (start_btn_default) {
				start_btn.removeClass("btn-default").addClass("btn-primary");
			}
		}

		// Finish Button
		let show_finish_button = (
			erpnext.manufacturing.has_stock_entry_permission()
			&& (
				(doc.skip_transfer && flt(doc.produced_qty) < qty_with_allowance)
				|| (!doc.skip_transfer && flt(doc.produced_qty) + flt(doc.process_loss_qty) < flt(doc.material_transferred_for_manufacturing))
			)
		)

		let finish_button_primary = (
			(doc.skip_transfer && flt(doc.produced_qty) < flt(doc.producible_qty)) ||
			(!doc.skip_transfer && flt(doc.material_transferred_for_manufacturing) > flt(doc.produced_qty) + flt(doc.process_loss_qty))
		)

		if (show_finish_button) {
			let finish_btn = this.frm.add_custom_button(__("Finish"), () => {
				erpnext.manufacturing.finish_work_order(doc);
			});

			if (finish_button_primary) {
				finish_btn.removeClass("btn-default").addClass("btn-primary");
			}
		}

		// If "Material Consumption is check in Manufacturing Settings, allow Material Consumption
		let show_consumption_button = (
			erpnext.manufacturing.has_stock_entry_permission()
			&& !doc.skip_transfer
			&& doc.allow_material_consumption
			&& flt(doc.produced_qty) + flt(doc.process_loss_qty) < flt(doc.material_transferred_for_manufacturing)
			&& (doc.required_items || []).some(d => flt(d.consumed_qty) < flt(d.required_qty))
		)

		if (show_consumption_button) {
			this.frm.add_custom_button(__('Material Consumption Entry'), () => {
				return erpnext.manufacturing.make_stock_entry_from_work_order(this.frm.doc, "Material Consumption for Manufacture");
			}, __("Create"));
		}
	}

	setup_progressbars() {
		if (this.frm.doc.docstatus == 1) {
			this.show_progress_for_production();
			this.show_progress_for_operations();
			this.show_progress_for_packing();
		}
	}

	show_progress_for_production() {
		erpnext.manufacturing.show_progress_for_production(this.frm.doc, this.frm, {
			show_rejection_reconciliation: !this.frm.doc.packing_slip_required,
		});
	}

	show_progress_for_packing() {
		if (this.frm.doc.packing_slip_required) {
			erpnext.manufacturing.show_progress_for_packing(this.frm.doc, this.frm);
		}
	}

	show_progress_for_operations() {
		if (this.frm.doc.producible_qty && this.frm.doc.operations && this.frm.doc.operations.length) {
			for (let row of this.frm.doc.operations) {
				erpnext.manufacturing.show_progress_for_operation(this.frm.doc, row, this.frm);
			}
		}
	}

	production_item() {
		if (this.frm.doc.production_item) {
			return frappe.call({
				method: "erpnext.manufacturing.doctype.work_order.work_order.get_item_details",
				args: {
					item: this.frm.doc.production_item,
					project: this.frm.doc.project
				},
				freeze: true,
				callback: (r) => {
					if (r.message) {
						this.frm.in_production_item_onchange = true;

						$.each(["item_name", "description", "stock_uom", "project", "bom_no",
							"transfer_material_against"], (i, field) => {
							this.frm.set_value(field, r.message[field]);
						});

						if (r.message["set_scrap_wh_mandatory"]) {
							this.frm.toggle_reqd("scrap_warehouse", true);
						}

						this.frm.in_production_item_onchange = false;
					}
				}
			});
		}
	}

	bom_no() {
		if (this.frm.doc.bom_no) {
			return this.frm.call({
				method: "get_items_and_operations_from_bom",
				doc: this.frm.doc,
				freeze: true,
				callback: (r) => {
					if (r.message["set_scrap_wh_mandatory"]) {
						this.frm.toggle_reqd("scrap_warehouse", true);
					}
				}
			});
		}
	}

	use_multi_level_bom() {
		if (this.frm.doc.bom_no) {
			return this.bom_no();
		}
	}

	project() {
		if (!this.frm.in_production_item_onchange && !this.frm.doc.bom_no) {
			return this.production_item();
		}
	}

	qty() {
		return this.bom_no();
	}

	source_warehouse(doc, cdt, cdn) {
		if (cdt == "Work Order") {
			erpnext.utils.autofill_warehouse(this.frm.doc.required_items, "source_warehouse", this.frm.doc.source_warehouse);
		} else {
			let row = frappe.get_doc(cdt, cdn);
			if (row.item_code && row.source_warehouse) {
				frappe.call({
					method: "erpnext.stock.utils.get_latest_stock_qty",
					args: {
						item_code: row.item_code,
						warehouse: row.source_warehouse
					},
					callback: (r) => {
						frappe.model.set_value(row.doctype, row.name, "available_qty_at_source_warehouse", flt(r.message));
					}
				});
			}
		}
	}

	workstation(frm, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		if (row.workstation) {
			frappe.call({
				method: "frappe.client.get",
				args: {
					doctype: "Workstation",
					name: row.workstation
				},
				callback: (r) => {
					frappe.model.set_value(row.doctype, row.name, "hour_rate", r.message.hour_rate);
				}
			});
		}
	}

	hour_rate() {
		this.calculate_cost();
	}

	time_in_mins() {
		this.calculate_cost();
	}

	rate() {
		this.calculate_cost();
	}

	additional_operating_cost() {
		this.calculate_cost();
	}

	calculate_cost() {
		let doc = this.frm.doc;

		doc.planned_operating_cost = 0.0;
		for (let d of doc.operations || []) {
			let planned_operating_cost = flt(flt(d.hour_rate) * flt(d.time_in_mins) / 60, 2);
			frappe.model.set_value(d.doctype, d.name, "planned_operating_cost", planned_operating_cost);
			doc.planned_operating_cost += planned_operating_cost;
		}

		doc.additional_operating_cost = 0;
		for (let d of doc.additional_costs || []) {
			let amount = flt(flt(d.rate) * flt(doc.qty), precision('amount', d));
			frappe.model.set_value(d.doctype, d.name, "amount", amount);
			doc.additional_operating_cost += amount;
		}

		this.frm.refresh_field('planned_operating_cost');
		this.frm.refresh_field('additional_operating_cost');

		let variable_cost = flt(doc.actual_operating_cost) || flt(doc.planned_operating_cost);
		this.frm.set_value("total_operating_cost", variable_cost + doc.additional_operating_cost);
		this.frm.set_value("total_cost", doc.total_operating_cost + flt(doc.raw_material_cost));
	}

	set_default_warehouse() {
		if (!this.frm.doc.source_warehouse || !this.frm.doc.wip_warehouse || !this.frm.doc.fg_warehouse) {
			return frappe.call({
				method: "erpnext.manufacturing.doctype.work_order.work_order.get_default_warehouse",
				callback: (r) => {
					if (r.message) {
						for (let [field, value] of Object.entries(r.message)) {
							if (!this.frm.doc[field]) {
								this.frm.set_value(field, r.message[field]);
							}
						}
					}
				}
			});
		}
	}

	make_packing_slip() {
		return frappe.call({
			method: "erpnext.manufacturing.doctype.work_order.work_order.make_packing_slip",
			args: {
				work_orders: [this.frm.doc.name],
			},
			callback: (r) => {
				let doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		});
	}

	make_purchase_order() {
		return frappe.call({
			method: "erpnext.manufacturing.doctype.work_order.work_order.make_purchase_order",
			args: {
				work_orders: [this.frm.doc.name],
			},
			callback: (r) => {
				let doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		});
	}

	make_pick_list(purpose) {
		return erpnext.manufacturing.show_qty_prompt_for_stock_entry(this.frm.doc, purpose).then((r) => {
			return frappe.xcall("erpnext.manufacturing.doctype.work_order.work_order.create_pick_list", {
				"source_name": this.frm.doc.name,
				"for_qty": r.data.qty
			});
		}).then((pick_list) => {
			frappe.model.sync(pick_list);
			frappe.set_route('Form', pick_list.doctype, pick_list.name);
		});
	}

	create_job_cards() {
		let operations_data = [];

		const dialog = frappe.prompt({
			fieldname: 'operations', fieldtype: 'Table', label: __('Operations'),
			fields: [
				{
					fieldtype:'Link',
					fieldname:'operation',
					label: __('Operation'),
					read_only: 1,
					in_list_view: 1
				},
				{
					fieldtype:'Link',
					fieldname:'workstation',
					label: __('Workstation'),
					read_only: 1,
					in_list_view: 1
				},
				{
					fieldtype:'Data',
					fieldname:'name',
					label: __('Operation Id')
				},
				{
					fieldtype:'Float',
					fieldname:'pending_qty',
					label: __('Pending Qty'),
				},
				{
					fieldtype:'Float',
					fieldname:'qty',
					label: __('Quantity to Manufacture'),
					read_only: 0,
					in_list_view: 1,
				},
			],
			data: operations_data,
			in_place_edit: true,
			get_data: () => {
				return operations_data;
			}
		}, (data) => {
			return frappe.call({
				method: "erpnext.manufacturing.doctype.work_order.work_order.create_job_cards",
				args: {
					work_order: this.frm.doc.name,
					operations: data.operations,
				}
			});
		}, __("Job Card"), __("Create"));

		dialog.fields_dict["operations"].grid.wrapper.find('.grid-add-row').hide();

		let pending_qty = 0;
		this.frm.doc.operations.forEach(data => {
			if(data.completed_qty != this.frm.doc.qty) {
				pending_qty = this.frm.doc.qty - flt(data.completed_qty);

				dialog.fields_dict.operations.df.data.push({
					'name': data.name,
					'operation': data.operation,
					'workstation': data.workstation,
					'qty': pending_qty,
					'pending_qty': pending_qty,
				});
			}
		});
		dialog.fields_dict.operations.grid.refresh();
	}

	make_bom() {
		return this.frm.call({
			method: "make_bom",
			doc: this.frm.doc,
			callback: (r) => {
				if (r.message) {
					let doc = frappe.model.sync(r.message)[0];
					frappe.set_route("Form", doc.doctype, doc.name);
				}
			}
		});
	}

	make_sub_assembly_work_orders() {
		return this.frm.call({
			method: "get_sub_assembly_items",
			doc: this.frm.doc,
			callback: (r) => {
				if (!r.message || !r.message.length) {
					frappe.msgprint({
						title: __('Work Orders not created'),
						message: __('No pending Sub-Assembly Items with Bill of Materials to Manufacture'),
						indicator: 'orange'
					});
					return;
				}
				return erpnext.manufacturing.make_work_orders_from_order_dialog(r.message, this.frm.doc, true);
			}
		});
	}

	stop_work_order(status) {
		return frappe.call({
			method: "erpnext.manufacturing.doctype.work_order.work_order.stop_unstop",
			args: {
				work_order: this.frm.doc.name,
				status: status,
			},
			callback: (r) => {
				if (r.message) {
					this.frm.reload_doc();
				}
			}
		});
	}
}

extend_cscript(cur_frm.cscript, new erpnext.manufacturing.WorkOrderController({frm: cur_frm}));
