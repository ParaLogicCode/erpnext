// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.accounts");
{% include 'erpnext/public/js/controllers/buying.js' %};

erpnext.accounts.PurchaseInvoice = class PurchaseInvoice extends erpnext.buying.BuyingController {
	setup(doc) {
		this.setup_posting_date_time_check();
		super.setup(doc);
	}
	onload() {
		super.onload();

		if (!this.frm.doc.__islocal) {
			// show credit_to in print format
			if (!this.frm.doc.supplier && this.frm.doc.credit_to) {
				this.frm.set_df_property("credit_to", "print_hide", 0);
			}
		}
	}

	// item_code(doc, cdt, cdn) {
	// 	let row = frappe.get_doc(cdt, cdn);
	// 	console.log(row.customs_tariff_no);
	// 	// this.get_item_details(row);

	// }

	// charge_type(doc, cdt, cdn) {
	// 	let row = frappe.get_doc(cdt, cdn);
	// 	this.populate_customs_tariff_number_table(cdt,cdn)
	// }

	// account_head(doc,cdt,cdn){
	// 	let row = frappe.get_doc(cdt, cdn);
	// 	this.populate_customs_tariff_number_table(cdt,cdn)
	// }

	// before_taxes_remove(doc, cdt, cdn){
	// 	console.log("hello");
	// 	let row = frappe.get_doc(cdt, cdn);
	// 	console.log(row.account_head);
	// 	this.delete_rows(cdt, cdn)
	// }




	populate_customs_tariff_number_table(cdt, cdn) {
		let numberOfItems = this.frm.doc.items.length;
		console.log(numberOfItems); // Output the number of items to the console
		console.log(this.frm.doc.items[0])
		let row = frappe.get_doc(cdt, cdn)
		let customs_tariff_no = this.frm.doc.items[0].customs_tariff_no;
		console.log(customs_tariff_no);
		if (row.charge_type == "On HS Code" && row.account_head!==undefined && customs_tariff_no!==undefined){
			for (let i = 0; i < this.frm.doc.items.length; i++){
				console.log(i);
				this.frm.toggle_display('customs_tariff_tax', true);
				let rows = this.frm.add_child('customs_tariff_tax', {
					account_head: row.account_head,
					customs_tariff_number: this.frm.doc.items[i].customs_tariff_no
				});
		}
			this.frm.debounced_refresh_fields();
		}
	}

	// amount(doc, cdt, cdn){
	// 	console.log("hello amount");
	// 	let row = frappe.get_doc(cdt, cdn);
	// 	let total_cost = this.frm.doc.items[0].amount;
	// 	let qty = this.frm.doc.items[0].qty;
	// 	this.frm.doc.total_taxes_and_charges = (row.amount/total_cost)*qty;
	// 	console.log(this.frm.doc.total_taxes_and_charges)
	// }



	// this -> controller class
	// this.frm -> form object
	// this.frm.doc -> document object (data model)


	refresh(doc) {
		const me = this;
		super.refresh();

		hide_fields(this.frm.doc);
		// Show / Hide button
		this.show_general_ledger();

		if(doc.update_stock==1 && doc.docstatus==1) {
			this.show_stock_ledger();
		}

		if (me.frm.doc.docstatus == 0) {
			me.add_get_latest_price_button();
		}
		if (me.frm.doc.docstatus == 1) {
			me.add_update_price_list_button();
		}

		if (!doc.is_return && doc.docstatus == 1 && doc.outstanding_amount != 0) {
			if(doc.on_hold) {
				this.frm.add_custom_button(
					__('Change Release Date'),
					function() {me.change_release_date()},
					__('Hold Invoice')
				);
				this.frm.add_custom_button(
					__('Unblock Invoice'),
					function () { me.unblock_invoice() },
					__('Hold Invoice')
				);
			} else if (!doc.on_hold) {
				this.frm.add_custom_button(
					__('Block Invoice'),
					function () { me.block_invoice() },
					__('Hold Invoice')
				);
			}
		}

		if (
			doc.docstatus == 1
			&& doc.outstanding_amount != 0
			&& !(doc.is_return && doc.return_against)
			&& (frappe.model.can_create("Payment Entry") || frappe.model.can_create("Journal Entry"))
		) {
			this.frm.add_custom_button(__('Payment'), this.make_payment_entry,
				__('Create'));
			this.frm.page.set_inner_btn_group_as_primary(__('Create'));
		}

		if (!doc.is_return && doc.docstatus == 1) {
			if (
				(doc.outstanding_amount >= 0 || Math.abs(flt(doc.outstanding_amount)) < flt(doc.grand_total))
				&& frappe.model.can_create("Purchase Invoice")
			) {
				this.frm.add_custom_button(__('Return / Debit Note'), this.make_debit_note,
					__('Create'));
			}

			if (doc.update_stock && frappe.model.can_create("Landed Cost Voucher")) {
				this.frm.add_custom_button(__('Landed Cost Voucher'), this.make_landed_cost_voucher,
					__("Create"));
			}

			if (!doc.auto_repeat && frappe.model.can_create("Auto Repeat")) {
				this.frm.add_custom_button(__('Subscription'), function () {
					erpnext.utils.make_subscription(doc.doctype, doc.name)
				}, __('Create'))
			}
		}

		if (doc.outstanding_amount > 0 && !cint(doc.is_return) && frappe.model.can_create("Payment Request")) {
			this.frm.add_custom_button(__('Payment Request'), function () {
				me.make_payment_request()
			}, __('Create'));
		}

		if (doc.docstatus === 0) {
			if (frappe.model.can_read("Purchase Receipt")) {
				this.frm.add_custom_button(__('Purchase Receipt'), function () {
					erpnext.utils.map_current_doc({
						method: "erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice",
						source_doctype: "Purchase Receipt",
						target: me.frm,
						date_field: "posting_date",
						setters: [
							{
								fieldtype: 'Link',
								label: __('Supplier'),
								options: 'Supplier',
								fieldname: 'supplier',
								default: me.frm.doc.supplier || undefined,
							},
							{
								fieldtype: 'Data',
								label: __('Bill No'),
								fieldname: 'bill_no',
							},
							{
								fieldtype: 'DateRange',
								label: __('Date Range'),
								fieldname: 'posting_date',
							}
						],
						columns: ['supplier_name', 'bill_no', 'posting_date'],
						get_query_filters: {
							supplier: me.frm.doc.supplier || undefined,
							docstatus: 1,
							status: ["not in", ["Closed", "Completed"]],
							billing_status: "To Bill",
							company: me.frm.doc.company,
							is_return: me.frm.doc.is_return
						}
					})
				}, __("Get Items From"));
			}

			if (frappe.model.can_read("Purchase Order")) {
				this.frm.add_custom_button(__('Purchase Order'), function () {
					erpnext.utils.map_current_doc({
						method: "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
						source_doctype: "Purchase Order",
						target: me.frm,
						setters: [
							{
								fieldtype: 'Link',
								label: __('Supplier'),
								options: 'Supplier',
								fieldname: 'supplier',
								default: me.frm.doc.supplier || undefined,
							},
							{
								fieldtype: 'DateRange',
								label: __('Date Range'),
								fieldname: 'transaction_date',
							}
						],
						columns: ['supplier_name', 'transaction_date'],
						get_query_filters: {
							supplier: me.frm.doc.supplier || undefined,
							docstatus: 1,
							status: ["not in", ["Closed", "On Hold"]],
							billing_status: "To Bill",
							company: me.frm.doc.company
						}
					})
				}, __("Get Items From"));
			}
		}

		if (doc.docstatus == 1 && !doc.inter_company_reference) {
			if (me.frm.doc.__onload?.is_internal_supplier) {
				me.frm.add_custom_button("Inter Company Invoice", function () {
					me.make_inter_company_invoice(me.frm);
				}, __('Create'));
			}
		}

		// sales order
		if (doc.docstatus === 1 && !doc.is_return && frappe.model.can_create("Sales Order")) {
			this.frm.add_custom_button(__('Sales Order'), () => me.make_sales_order(),
				__("Create"));
		}
	}

	unblock_invoice() {
		const me = this;
		frappe.call({
			'method': 'erpnext.accounts.doctype.purchase_invoice.purchase_invoice.unblock_invoice',
			'args': { 'name': me.frm.doc.name },
			'callback': (r) => me.frm.reload_doc()
		});
	}

	block_invoice() {
		this.make_comment_dialog_and_block_invoice();
	}

	change_release_date() {
		this.make_dialog_and_set_release_date();
	}

	can_change_release_date(date) {
		const diff = frappe.datetime.get_diff(date, frappe.datetime.nowdate());
		if (diff < 0) {
			frappe.throw(__('New release date should be in the future'));
			return false;
		} else {
			return true;
		}
	}

	make_sales_order() {
		var me = this;
		var dialog = new frappe.ui.Dialog({
			title: __("Customer"),
			fields: [
				{ "fieldtype": "Link", "label": __("Customer"), "fieldname": "customer", "options": "Customer", "mandatory": true },
				{ "fieldtype": "Button", "label": __("Make Sales Order"), "fieldname": "make_sales_order", "cssClass": "btn-primary" },
			]
		});

		dialog.fields_dict.make_sales_order.$input.click(function () {
			var args = dialog.get_values();
			dialog.hide();
			return frappe.call({
				type: "GET",
				method: "erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_sales_order",
				args: {
					"customer": args.customer,
					"source_name": me.frm.doc.name
				},
				freeze: true,
				callback: function (r) {
					if (!r.exc) {
						var doc = frappe.model.sync(r.message);
						frappe.set_route("Form", r.message.doctype, r.message.name);
					}
				}
			})
		});
		dialog.show();
	}

	make_comment_dialog_and_block_invoice() {
		const me = this;

		const title = __('Block Invoice');
		const fields = [
			{
				fieldname: 'release_date',
				read_only: 0,
				fieldtype: 'Date',
				label: __('Release Date'),
				default: me.frm.doc.release_date,
				reqd: 1
			},
			{
				fieldname: 'hold_comment',
				read_only: 0,
				fieldtype: 'Small Text',
				label: __('Reason For Putting On Hold'),
				default: ""
			},
		];

		this.dialog = new frappe.ui.Dialog({
			title: title,
			fields: fields
		});

		this.dialog.set_primary_action(__('Save'), function () {
			const dialog_data = me.dialog.get_values();
			frappe.call({
				'method': 'erpnext.accounts.doctype.purchase_invoice.purchase_invoice.block_invoice',
				'args': {
					'name': me.frm.doc.name,
					'hold_comment': dialog_data.hold_comment,
					'release_date': dialog_data.release_date
				},
				'callback': (r) => me.frm.reload_doc()
			});
			me.dialog.hide();
		});

		this.dialog.show();
	}

	make_dialog_and_set_release_date() {
		const me = this;

		const title = __('Set New Release Date');
		const fields = [
			{
				fieldname: 'release_date',
				read_only: 0,
				fieldtype: 'Date',
				label: __('Release Date'),
				default: me.frm.doc.release_date
			},
		];

		this.dialog = new frappe.ui.Dialog({
			title: title,
			fields: fields
		});

		this.dialog.set_primary_action(__('Save'), function () {
			me.dialog_data = me.dialog.get_values();
			if (me.can_change_release_date(me.dialog_data.release_date)) {
				me.dialog_data.name = me.frm.doc.name;
				me.set_release_date(me.dialog_data);
				me.dialog.hide();
			}
		});

		this.dialog.show();
	}

	set_release_date(data) {
		return frappe.call({
			'method': 'erpnext.accounts.doctype.purchase_invoice.purchase_invoice.change_release_date',
			'args': data,
			'callback': (r) => this.frm.reload_doc()
		});
	}

	supplier() {
		this.set_party_details();
	}

	letter_of_credit() {
		erpnext.utils.get_party_account_details(this.frm);
	}

	set_party_details() {
		if (this.frm.updating_party_details)
			return;

		var me = this;
		return erpnext.utils.get_party_details(this.frm, "erpnext.accounts.party.get_party_details",
			{
				posting_date: this.frm.doc.posting_date,
				bill_date: this.frm.doc.bill_date,
				party_type: "Supplier",
				party: this.frm.doc.supplier,
				letter_of_credit: this.frm.doc.letter_of_credit,
				account: this.frm.doc.credit_to,
				price_list: this.frm.doc.buying_price_list
			}, function () {
				me.apply_pricing_rule();
				me.frm.doc.apply_tds = me.frm.supplier_tds ? 1 : 0;
				me.frm.doc.tax_withholding_category = me.frm.supplier_tds;
				me.frm.set_df_property("apply_tds", "read_only", me.frm.supplier_tds ? 0 : 1);
				me.frm.set_df_property("apply_tds", "hidden", me.frm.supplier_tds ? 0 : 1);
				me.frm.set_df_property("tax_withholding_category", "hidden", me.frm.supplier_tds ? 0 : 1);
			})
	}

	apply_tds(frm) {
		var me = this;

		if (!me.frm.doc.apply_tds) {
			me.frm.set_value("tax_withholding_category", '');
			me.frm.set_df_property("tax_withholding_category", "hidden", 1);
		} else {
			me.frm.set_value("tax_withholding_category", me.frm.supplier_tds);
			me.frm.set_df_property("tax_withholding_category", "hidden", 0);
		}
	}

	credit_to() {
		var me = this;
		if (this.frm.doc.credit_to) {
			me.frm.call({
				method: "frappe.client.get_value",
				args: {
					doctype: "Account",
					fieldname: "account_currency",
					filters: { name: me.frm.doc.credit_to },
				},
				callback: function (r, rt) {
					if (r.message) {
						me.frm.set_value("party_account_currency", r.message.account_currency);
						me.set_dynamic_labels();
					}
				}
			});
		}
	}

	make_inter_company_invoice(frm) {
		frappe.model.open_mapped_doc({
			method: "erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_inter_company_sales_invoice",
			frm: frm
		});
	}

	is_paid() {
		if (cint(this.frm.doc.is_paid)) {
			this.frm.set_value("allocate_advances_automatically", 0);
			if (!this.frm.doc.company) {
				this.frm.set_value("is_paid", 0)
				frappe.msgprint(__("Please specify Company to proceed"));
			}
		}
		this.calculate_outstanding_amount();
		this.frm.refresh_fields();
	}

	write_off_amount() {
		this.set_in_company_currency(this.frm.doc, ["write_off_amount"]);
		this.calculate_outstanding_amount();
		this.frm.refresh_fields();
	}

	paid_amount() {
		this.set_in_company_currency(this.frm.doc, ["paid_amount"]);
		this.write_off_amount();
		this.frm.refresh_fields();
	}

	allocated_amount() {
		this.calculate_total_advance();
		this.frm.refresh_fields();
	}

	items_add(doc, cdt, cdn) {
		var row = frappe.get_doc(cdt, cdn);
		this.frm.script_manager.copy_from_first_row("items", row,
			["expense_account", "project"]);
	}

	on_submit() {
		$.each(this.frm.doc["items"] || [], function (i, row) {
			if (row.purchase_receipt) frappe.model.clear_doc("Purchase Receipt", row.purchase_receipt)
		})
	}

	make_debit_note() {
		frappe.model.open_mapped_doc({
			method: "erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_debit_note",
			frm: cur_frm
		})
	}
};

cur_frm.script_manager.make(erpnext.accounts.PurchaseInvoice);

// Hide Fields
// ------------
function hide_fields(doc) {
	var item_fields_stock = ['warehouse_section', 'received_qty', 'rejected_qty'];

	cur_frm.fields_dict['items'].grid.set_column_disp(item_fields_stock,
		(cint(doc.update_stock) == 1 || cint(doc.is_return) == 1 ? true : false));
}

cur_frm.cscript.update_stock = function (doc, dt, dn) {
	hide_fields(doc, dt, dn);
	this.frm.fields_dict.items.grid.toggle_reqd("item_code", doc.update_stock ? true : false)
}

cur_frm.fields_dict.cash_bank_account.get_query = function (doc) {
	return {
		filters: [
			["Account", "account_type", "in", ["Cash", "Bank"]],
			["Account", "is_group", "=", 0],
			["Account", "company", "=", doc.company],
			["Account", "report_type", "=", "Balance Sheet"]
		]
	}
}

cur_frm.fields_dict['items'].grid.get_field("item_code").get_query = function (doc, cdt, cdn) {
	return {
		query: "erpnext.controllers.queries.item_query",
		filters: { 'is_purchase_item': 1 }
	}
}

cur_frm.fields_dict['credit_to'].get_query = function (doc) {
	// filter on Account
	return {
		filters: {
			'account_type': 'Payable',
			'is_group': 0,
			'company': doc.company
		}
	}
}

// Get Print Heading
cur_frm.fields_dict['select_print_heading'].get_query = function (doc, cdt, cdn) {
	return {
		filters: [
			['Print Heading', 'docstatus', '!=', 2]
		]
	}
}

cur_frm.set_query("expense_account", "items", function (doc) {
	return {
		query: "erpnext.controllers.queries.get_expense_account",
		filters: { 'company': doc.company }
	}
});

cur_frm.cscript.expense_account = function (doc, cdt, cdn) {
	var d = locals[cdt][cdn];
	if (d.idx == 1 && d.expense_account) {
		var cl = doc.items || [];
		for (var i = 0; i < cl.length; i++) {
			if (!cl[i].expense_account) cl[i].expense_account = d.expense_account;
		}
	}
	refresh_field('items');
}

cur_frm.fields_dict["items"].grid.get_field("cost_center").get_query = function (doc) {
	return {
		filters: {
			'company': doc.company,
			'is_group': 0
		}

	}
}

cur_frm.fields_dict['items'].grid.get_field('project').get_query = function (doc, cdt, cdn) {
	return {
		filters: [
			['Project', 'status', 'not in', 'Completed, Cancelled']
		]
	}
}

frappe.ui.form.on("Purchase Invoice", {
	setup: function (frm) {
		frm.custom_make_buttons = {
			'Purchase Invoice': 'Return / Debit Note',
			'Payment Entry': 'Payment',
			'Sales Order': 'Sales Order',
			'Auto Repeat': 'Subscription',
			'Payment Request': 'Payment Request',
		}

		frm.fields_dict['items'].grid.get_field('deferred_expense_account').get_query = function (doc) {
			return {
				filters: {
					'root_type': 'Asset',
					'company': doc.company,
					"is_group": 0
				}
			}
		}

		frm.set_query("cost_center", function () {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0
				}
			};
		});
	},

	onload: function (frm) {
		if (frm.doc.__onload) {
			if (frm.doc.supplier) {
				frm.doc.apply_tds = frm.doc.__onload.supplier_tds ? 1 : 0;
			}
			if (!frm.doc.__onload.supplier_tds) {
				frm.set_df_property("apply_tds", "read_only", 1);
				me.frm.set_df_property("apply_tds", "hidden", 1);
			}
		}

		erpnext.queries.setup_queries(frm, "Warehouse", function () {
			return erpnext.queries.warehouse(frm.doc);
		});
	},

	is_subcontracted: function (frm) {
		if (frm.doc.is_subcontracted) {
			erpnext.buying.get_default_bom(frm);
		}
	}
})
