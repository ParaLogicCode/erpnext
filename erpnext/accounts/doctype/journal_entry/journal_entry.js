// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.accounts");

erpnext.accounts.JournalEntry = class JournalEntry extends frappe.ui.form.Controller {
	setup() {
		this.setup_queries();
		this.setup_balance_formatter();
		this.frm.add_fetch("bank_account", "account", "account");
	}

	refresh() {
		erpnext.toggle_naming_series();
		erpnext.hide_company();
		this.setup_buttons();
		this.voucher_type();
		this.toggle_fields_based_on_currency();
	}

	onload() {
		this.load_defaults();
	}

	validate() {
		this.update_totals();
	}

	onload_post_render() {
		this.frm.get_field("accounts").grid.set_multiple_add("account");
	}

	setup_buttons() {
		if (this.frm.doc.docstatus == 1) {
			this.frm.add_custom_button(__('Ledger'), () => {
				frappe.route_options = {
					"voucher_no": this.frm.doc.name,
					"from_date": this.frm.doc.posting_date,
					"to_date": this.frm.doc.posting_date,
					"company": this.frm.doc.company,
					"finance_book": this.frm.doc.finance_book,
					"merge_similar_entries": 0
				};
				frappe.set_route("query-report", "General Ledger");
			}, __('View'));
		}

		if (this.frm.doc.docstatus == 1) {
			this.frm.add_custom_button(__('Reverse Journal Entry'), () => {
				this.reverse_journal_entry();
			}, __('Create'));
		}

		if (this.frm.doc.docstatus == 1 && !this.frm.doc.inter_company_reference) {
			this.frm.add_custom_button(__("Inter Company Journal Entry"), () => {
				this.make_inter_company_journal_entry();
			}, __('Create'));
		}
	}

	setup_queries() {
		let me = this;

		me.frm.set_query("account", "accounts", (doc) => {
			let filters = {
				company: doc.company,
				is_group: 0,
			};
			if (!me.frm.doc.multi_currency) {
				filters["account_currency"] = frappe.get_doc(":Company", me.frm.doc.company).default_currency;
			}

			return {
				filters: filters
			};
		});

		me.frm.set_query("cost_center", "accounts", () => {
			return {
				filters: {
					company: me.frm.doc.company,
					is_group: 0
				}
			};
		});

		me.frm.set_query("cost_center", () => {
			return {
				filters: {
					company: me.frm.doc.company,
					is_group: 0
				}
			};
		});

		me.frm.set_query("party_type", "accounts", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: "erpnext.setup.doctype.party_type.party_type.get_party_type",
				filters: {
					'account': row.account
				}
			};
		});

		me.frm.set_query("reference_name", "accounts", (doc, cdt, cdn) => {
			let row = frappe.get_doc(cdt, cdn);
			return this.reference_document_query(row);
		});
	}

	reference_document_query(row) {
		// journal entry
		if (row.reference_type === "Journal Entry") {
			frappe.model.validate_missing(row, "account");
			frappe.model.validate_missing(row, "party_type");
			frappe.model.validate_missing(row, "party");
			return {
				query: "erpnext.accounts.doctype.journal_entry.journal_entry.get_against_jv",
				filters: {
					account: row.account,
					party_type: row.party_type,
					party: row.party
				}
			};
		}

		// payroll entry
		if (row.reference_type === "Payroll Entry") {
			return {
				query: "erpnext.hr.doctype.payroll_entry.payroll_entry.get_payroll_entries_for_jv",
			};
		}

		let party_account_field;
		let out = {
			filters: [
				[row.reference_type, "docstatus", "=", 1]
			]
		};

		if (in_list(["Sales Invoice", "Purchase Invoice", "Landed Cost Voucher", "Expense Claim"], row.reference_type)) {
			out.filters.push([row.reference_type, "outstanding_amount", "!=", 0]);
			// account filter
			if (row.reference_type == "Expense Claim") {
				party_account_field = "payable_account";
			} else {
				party_account_field = row.reference_type==="Sales Invoice" ? "debit_to": "credit_to";
			}
		}

		if (in_list(["Sales Order", "Purchase Order"], row.reference_type)) {
			frappe.model.validate_missing(row, "account");
			out.filters.push([row.reference_type, "per_billed", "=", 0]);
		}

		if (row.reference_type == "Employee Advance") {
			party_account_field = "advance_account";
			out.filters.push([row.reference_type, "status", "in", ['Unpaid', 'Unclaimed']]);
		}

		if (row.reference_type == "Loan") {
			party_account_field = "loan_account";
		}

		if (party_account_field) {
			out.filters.push([row.reference_type, party_account_field, "=", row.account]);
		}

		if (row.party_type && row.party) {
			let party_field;
			if(row.reference_type == "Landed Cost Voucher") {
				out.filters.push([row.reference_type, "party_type", "=", row.party_type]);
				party_field = "party"

			} else if(row.reference_type.indexOf("Sales")===0) {
				if (row.reference_type == "Sales Invoice") {
					party_field = "bill_to";
				} else {
					party_field = "customer";
				}

			} else if (row.reference_type.indexOf("Purchase")===0) {
				party_field = "supplier";

			} else if (row.reference_type == "Loan") {
				out.filters.push([row.reference_type, "applicant_type", "=", row.party_type]);
				party_field = "applicant";

			} else if (['Employee Advance', 'Expense Claim'].includes(row.reference_type)) {
				party_field = "employee";

			}

			if (party_field) {
				out.filters.push([row.reference_type, party_field, "=", row.party]);
			}
		}

		return out;
	}

	posting_date() {
		if (!this.frm.doc.multi_currency || !this.frm.doc.posting_date) {
			return;
		}

		for (let row of this.frm.doc.accounts || []) {
			this.set_exchange_rate(row.doctype, row.name);
		}
	}

	company() {
		this.get_other_company_accounts_and_cost_centers();
	}

	multi_currency() {
		this.toggle_fields_based_on_currency();
	}

	voucher_type() {
		let doc = this.frm.doc;
		if (!doc.company) {
			return;
		}

		const update_jv_details = (doc, r) => {
			for (let d of r) {
				let row = frappe.model.add_child(doc, "Journal Entry Account", "accounts");
				row.account = d.account;
				row.balance = d.balance;
			}
			this.frm.refresh_field("accounts");
		}

		if((!(doc.accounts || []).length) || ((doc.accounts || []).length==1 && !doc.accounts[0].account)) {
			if (in_list(["Bank Entry", "Cash Entry"], doc.voucher_type)) {
				return frappe.call({
					type: "GET",
					method: "erpnext.accounts.doctype.journal_entry.journal_entry.get_default_bank_cash_account",
					args: {
						"account_type": (
							doc.voucher_type == "Bank Entry" ?
							"Bank"
							: (doc.voucher_type=="Cash Entry" ? "Cash" : null)
						),
						"company": doc.company
					},
					callback: (r) => {
						if (r.message?.account) {
							update_jv_details(doc, [r.message]);
						}
					}
				});
			} else if (doc.voucher_type == "Opening Entry") {
				return frappe.call({
					type:"GET",
					method: "erpnext.accounts.doctype.journal_entry.journal_entry.get_opening_accounts",
					args: {
						"company": doc.company
					},
					callback: (r) => {
						frappe.model.clear_table(doc, "accounts");
						if (r.message) {
							update_jv_details(doc, r.message);
						}
					}
				});
			}
		}
	}

	toggle_fields_based_on_currency() {
		let fields = ["currency_section", "account_currency", "exchange_rate", "debit", "credit"];

		let grid = this.frm.get_field("accounts").grid;
		if (grid) {
			grid.set_column_disp(fields, this.frm.doc.multi_currency);
		}

		// dynamic label
		let field_label_map = {
			"debit_in_account_currency": "Debit",
			"credit_in_account_currency": "Credit"
		};

		for (let [fieldname, label] of Object.entries(field_label_map)) {
			let df = frappe.meta.get_docfield("Journal Entry Account", fieldname, this.frm.doc.name);
			df.label = this.frm.doc.multi_currency ? (label + " in Account Currency") : label;
		}
	}

	load_defaults() {
		if (this.frm.doc.__islocal && this.frm.doc.company) {
			frappe.model.set_default_values(this.frm.doc);
			for (let jvd of this.frm.doc.accounts || []) {
				frappe.model.set_default_values(jvd);
			}

			let posting_date = this.frm.doc.posting_date;
			if (!this.frm.doc.amended_from) {
				this.frm.set_value('posting_date', posting_date || frappe.datetime.get_today());
			}
		}
	}

	setup_balance_formatter() {
		for (let field of ["balance", "party_balance"]) {
			let df = frappe.meta.get_docfield("Journal Entry Account", field);
			df.formatter = function(value, df, options, doc) {
				let currency = frappe.meta.get_field_currency(df, doc);
				let dr_or_cr = value ? (value > 0.0 ? __("Dr") : __("Cr")) : "";
				return `${(value == null || value === "") ? "" : format_currency(Math.abs(value), currency)} ${dr_or_cr}`;
			}
		}
	}

	accounts_add(doc, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);

		// set difference
		if (doc.difference) {
			if(doc.difference > 0) {
				row.credit_in_account_currency = doc.difference;
				row.credit = doc.difference;
			} else {
				row.debit_in_account_currency = -doc.difference;
				row.debit = -doc.difference;
			}
		}
		this.update_totals();
	}

	accounts_remove() {
		this.update_totals();
	}

	account(doc, cdt, cdn) {
		this.set_account_balance(cdt, cdn);
	}

	party(doc, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		if (row.party_type && row.party) {
			if (!this.frm.doc.company) {
				frappe.throw(__("Please select Company"));
			}
			return this.frm.call({
				method: "erpnext.accounts.doctype.journal_entry.journal_entry.get_party_account_and_balance",
				child: row,
				args: {
					company: this.frm.doc.company,
					party_type: row.party_type,
					party: row.party,
					cost_center: row.cost_center,
					account: row.account,
					date: this.frm.doc.posting_date,
				}
			});
		}
	}

	cost_center(doc, cdt, cdn) {
		if (cdt && cdn) {
			this.set_account_balance(cdt, cdn);
		}
	}

	debit_in_account_currency(doc, cdt, cdn) {
		this.set_exchange_rate(cdt, cdn);
	}

	credit_in_account_currency(doc, cdt, cdn) {
		this.set_exchange_rate(cdt, cdn);
	}

	debit() {
		this.update_totals();
	}

	credit() {
		this.update_totals();
	}

	exchange_rate(doc, cdt, cdn) {
		let row = locals[cdt][cdn];
		let company_currency = frappe.get_doc(":Company", this.frm.doc.company).default_currency;

		if(row.account_currency == company_currency || !this.frm.doc.multi_currency) {
			frappe.model.set_value(cdt, cdn, "exchange_rate", 1);
		}

		this.set_debit_credit_in_company_currency(cdt, cdn);

		if (this.frm.doc.multi_currency && (!row.exchange_rate || flt(row.exchange_rate) == 1)) {
			this.set_exchange_rate(cdt, cdn, true);
		}
	}

	reference_type(doc, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, 'reference_name', '');
	}

	reference_name(doc, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);

		if (row.reference_name) {
			this.get_outstanding(row.reference_type, row.reference_name, row);
			this.set_exchange_rate(cdt, cdn);
		}
	}

	get_outstanding(reference_type, reference_name, row) {
		let args = {
			"doctype": reference_type,
			"docname": reference_name,
			"party": row.party,
			"party_type": row.party_type,
			"account": row.account,
			"account_currency": row.account_currency,
			"company": this.frm.doc.company,
		}

		return frappe.call({
			method: "erpnext.accounts.doctype.journal_entry.journal_entry.get_outstanding",
			args: {
				args: args
			},
			callback: (r) => {
				if (r.message) {
					for (let [field, value] of Object.entries(r.message)) {
						if(['debit_in_account_currency', 'credit_in_account_currency', 'debit', 'credit'].includes(field)) {
							if(!row.debit_in_account_currency && !row.credit_in_account_currency) {
								frappe.model.set_value(row.doctype, row.name, field, value);
							}
						} else {
							frappe.model.set_value(row.doctype, row.name, field, value);
						}
					}
				}
			}
		});
	}

	set_exchange_rate(cdt, cdn, force=false) {
		let row = locals[cdt][cdn];
		let company_currency = frappe.get_doc(":Company", this.frm.doc.company).default_currency;

		if (row.account_currency == company_currency || !this.frm.doc.multi_currency) {
			frappe.model.set_value(cdt, cdn, "exchange_rate", 1);
			this.set_debit_credit_in_company_currency(cdt, cdn);
		} else if (force || !row.exchange_rate || row.exchange_rate == 1 || row.account_type == "Bank") {
			frappe.call({
				method: "erpnext.accounts.doctype.journal_entry.journal_entry.get_exchange_rate",
				args: {
					posting_date: this.frm.doc.posting_date,
					account: row.account,
					party_type: row.party_type,
					party: row.party,
					account_currency: row.account_currency,
					company: this.frm.doc.company,
					reference_type: cstr(row.reference_type),
					reference_name: cstr(row.reference_name),
					debit: flt(row.debit_in_account_currency),
					credit: flt(row.credit_in_account_currency),
					exchange_rate: row.exchange_rate
				},
				callback: (r) => {
					if (r.message) {
						frappe.model.set_value(cdt, cdn, "exchange_rate", flt(r.message));
						this.set_debit_credit_in_company_currency(cdt, cdn);
					}
				}
			})
		} else {
			this.set_debit_credit_in_company_currency(cdt, cdn);
		}
	}

	set_account_balance(cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.account) {
			if (!this.frm.doc.company) {
				frappe.throw(__("Please select Company first"));
			}
			if (!this.frm.doc.posting_date) {
				frappe.throw(__("Please select Posting Date first"));
			}

			return frappe.call({
				method: "erpnext.accounts.doctype.journal_entry.journal_entry.get_account_balance_and_party_type",
				args: {
					account: row.account,
					date: this.frm.doc.posting_date,
					company: this.frm.doc.company,
					debit: flt(row.debit_in_account_currency),
					credit: flt(row.credit_in_account_currency),
					exchange_rate: row.exchange_rate,
					cost_center: row.cost_center
				},
				callback: (r) => {
					if (r.message) {
						$.extend(row, r.message);
						this.set_debit_credit_in_company_currency(cdt, cdn);
						this.frm.refresh_field('accounts');
					}
				}
			});
		}
	}

	set_debit_credit_in_company_currency(cdt, cdn) {
		let row = locals[cdt][cdn];

		frappe.model.set_value(cdt, cdn, "debit",
			flt(flt(row.debit_in_account_currency) * flt(row.exchange_rate), precision("debit", row))
		);

		frappe.model.set_value(cdt, cdn, "credit",
			flt(flt(row.credit_in_account_currency) * flt(row.exchange_rate), precision("credit", row))
		);

		this.update_totals();
	}

	update_totals() {
		let doc = this.frm.doc;

		let total_debit = 0.0;
		let total_credit = 0.0;
		let accounts = doc.accounts || [];
		for (let d of accounts) {
			total_debit += flt(d.debit, precision("debit", d));
			total_credit += flt(d.credit, precision("credit", d));
		}
		doc.total_debit = total_debit;
		doc.total_credit = total_credit;
		doc.difference = flt((total_debit - total_credit), precision("difference"));
		refresh_many(["total_debit", "total_credit", "difference"]);
	}

	reverse_journal_entry() {
		frappe.model.open_mapped_doc({
			method: "erpnext.accounts.doctype.journal_entry.journal_entry.make_reverse_journal_entry",
			frm: this.frm,
		})
	}

	get_other_company_accounts_and_cost_centers() {
		let accounts = [];
		let cost_centers = [];

		if (this.frm.doc.cost_center) {
			cost_centers.push(this.frm.doc.cost_center);
		}

		for (let d of this.frm.doc.accounts || []) {
			if (d.account) {
				accounts.push(d.account);
			}
			if (d.cost_center) {
				cost_centers.push(d.cost_center);
			}
		}

		return frappe.call({
			method: "erpnext.accounts.doctype.journal_entry.journal_entry.get_other_company_accounts_and_cost_centers",
			args: {
				target_company: this.frm.doc.company,
				accounts: accounts,
				cost_centers: cost_centers,
			},
			callback: (r) =>{
				if (r.message) {
					this.frm.set_value(
						"cost_center",
						r.message.cost_centers[this.frm.doc.cost_center] || r.message.default_cost_center
					);

					for (let d of this.frm.doc.accounts || []) {
						if (d.account && r.message.accounts[d.account]) {
							frappe.model.set_value(d.doctype, d.name, "account", r.message.accounts[d.account]);
						}
						if (d.cost_center && r.message.cost_centers[d.cost_center]) {
							frappe.model.set_value(d.doctype, d.name, "cost_center", r.message.cost_centers[d.cost_center]);
						}
					}
				}
			}
		});
	}

	make_inter_company_journal_entry() {
		let dialog = new frappe.ui.Dialog({
			title: __("Select Company"),
			fields: [
				{
					'fieldname': 'company',
					'fieldtype': 'Link',
					'label': __('Company'),
					'options': 'Company',
					"get_query": () => {
						return {
							filters: [
								["Company", "name", "!=", this.frm.doc.company]
							]
						};
					},
					'reqd': 1
				}
			],
		});

		dialog.set_primary_action(__('Create'), () => {
			dialog.hide();
			let args = dialog.get_values();
			frappe.call({
				args: {
					"name": this.frm.doc.name,
					"voucher_type": this.frm.doc.voucher_type,
					"company": args.company
				},
				method: "erpnext.accounts.doctype.journal_entry.journal_entry.make_inter_company_journal_entry",
				callback: (r) => {
					if (r.message) {
						let doc = frappe.model.sync(r.message)[0];
						frappe.set_route("Form", doc.doctype, doc.name);
					}
				}
			});
		});

		dialog.show();
	}

	get_balance() {
		this.update_totals();
		this.frm.call("get_balance", null, () => {
			this.frm.refresh_fields();
		});
	}
};

cur_frm.script_manager.make(erpnext.accounts.JournalEntry);
