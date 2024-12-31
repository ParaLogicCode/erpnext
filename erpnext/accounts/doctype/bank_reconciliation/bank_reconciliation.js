// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.ui.form.on("Bank Reconciliation", {
	setup(frm) {
		frm.events.setup_queries(frm);
	},

	refresh(frm) {
		erpnext.hide_company(frm);
		if (!frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_user_default("Company"));
		}

		frm.disable_save();
		frm.page.set_primary_action(__("Update Clearance"), () => {
			frappe.confirm(__("Are you sure you update Bank Clearance?"), () => {
				frm.events.update_clearance(frm);
			});
		});

		if (!frm.doc.from_date) {
			frm.set_value("from_date", frappe.datetime.month_start());
		}
		if (!frm.doc.to_date) {
			frm.set_value("to_date", frappe.datetime.month_end());
		}
	},

	setup_queries(frm) {
		frm.set_query("bank_account", function() {
			return {
				filters: {
					"company": frm.doc.company,
					"is_company_account": 1,
				}
			};
		});

		frm.set_query("account", function() {
			return {
				filters: {
					"company": frm.doc.company,
					"account_type": ["in", ["Bank", "Cash"]],
					"is_group": 0
				}
			};
		});

		frm.set_query("suspense_account", function() {
			return {
				filters: {
					"company": frm.doc.company,
					"account_type": ["in", ["Bank", "Cash"]],
					"is_group": 0
				}
			};
		});
	},

	from_date(frm) {
		frm.events.get_opening_balance(frm);
	},

	account(frm) {
		frm.events.get_opening_balance(frm);
	},

	opening_balance(frm) {
		frm.events.calculate_totals(frm);
	},

	closing_balance(frm) {
		frm.events.calculate_totals(frm);
	},

	calculate_totals(frm) {
		frm.doc.total_amount = 0;
		frm.doc.cleared_amount = 0;
		for (let d of frm.doc.payment_entries || []) {
			frm.doc.total_amount += flt(d.amount);
			if (d.clearance_date) {
				frm.doc.cleared_amount += flt(d.amount);
			}
		}

		frm.doc.difference = flt(frm.doc.closing_balance) - flt(frm.doc.opening_balance) - flt(frm.doc.cleared_amount);

		frm.refresh_field("total_amount");
		frm.refresh_field("cleared_amount");
		frm.refresh_field("difference");
	},

	get_opening_balance(frm) {
		if (frm.doc.account && frm.doc.from_date) {
			return frappe.call({
				method: "erpnext.accounts.doctype.bank_reconciliation.bank_reconciliation.get_opening_balance",
				args: {
					account: frm.doc.account,
					from_date: frm.doc.from_date,
				},
				callback: function(r) {
					frm.set_value("opening_balance", flt(r.message));
				}
			});
		} else {
			frm.set_value("opening_balance", 0);
		}
	},

	update_clearance(frm) {
		return frappe.call({
			method: "update_clearance",
			doc: frm.doc,
			callback: function(r) {
				frm.refresh_fields();
				frm.events.calculate_totals(frm);
			}
		});
	},

	get_payment_entries(frm) {
		return frappe.call({
			method: "set_payment_entries",
			doc: frm.doc,
			callback: function(r) {
				frm.refresh_fields();
				frm.events.calculate_totals(frm);
			}
		});
	},
});

frappe.ui.form.on("Bank Reconciliation Detail", {
	clearance_date(frm) {
		frm.events.calculate_totals(frm);
	},
});
