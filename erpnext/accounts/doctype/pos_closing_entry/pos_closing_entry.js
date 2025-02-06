// Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('POS Closing Entry', {
	setup(frm) {
		frm.set_query("user", function (doc) {
			return {
				query: "erpnext.accounts.doctype.pos_profile.pos_profile.cashiers_query",
			};
		});

		frm.set_query('pos_profile', function(doc) {
			if(!doc.company) {
				frappe.throw(__('Please set Company'));
			}

			let filters = {
				company: doc.company,
			}
			if (doc.user) {
				filters["user"] = doc.user;
			}
			if (doc.branch) {
				filters["branch"] = doc.branch;
			}

			return {
				query: 'erpnext.accounts.doctype.pos_profile.pos_profile.pos_profile_query',
				filters: filters,
			};
		});
	},

	refresh(frm) {
		erpnext.hide_company(frm);

		// set default posting date / time
		if (frm.doc.docstatus == 0) {
			if (!frm.doc.period_end_date) {
				frm.set_value("period_end_date", frappe.datetime.now_datetime());
			}
			if (!frm.doc.period_end_time) {
				frm.set_value("period_end_time", frappe.datetime.now_time());
			}

			if (!frm.doc.user) {
				frm.set_value("user", frappe.session.user);
			}
		}

		if (frm.doc.docstatus == 1) {
			if (frm.doc.head_cashier_account) {
				frm.add_custom_button(__("Transfer to Head Cashier"), () => frm.events.make_head_cashier_voucher(frm),
					__("Create"));
			}
			frm.add_custom_button(__("Transfer to Clearing Accounts"), () => frm.events.make_till_transfer_voucher(frm),
				__("Create"));

			frm.page.set_inner_btn_group_as_primary(__("Create"));
		}
	},

	company(frm) {
		if (frm.doc.company) {
			frm.events.get_pos_profile(frm);
		}
	},

	branch(frm) {
		if (frm.doc.branch) {
			frm.events.get_pos_profile(frm);
		}
	},

	user(frm) {
		if (frm.doc.user) {
			frm.events.get_pos_profile(frm);
		}
	},

	get_pos_profile(frm) {
		if (frm.doc.user && frm.doc.company) {
			return frappe.call({
				method: "erpnext.accounts.doctype.pos_profile.pos_profile.get_pos_profile",
				args: {
					company: frm.doc.company,
					user: frm.doc.user,
					branch: frm.doc.branch,
				},
				callback: (r) => {
					if (r.message) {
						if (frm.doc.pos_profile == r.message) {
							frm.events.get_closing_voucher_details(frm);
						} else {
							frm.set_value("pos_profile", r.message);
						}
					}
				}
			});
		}
	},

	pos_profile(frm) {
		frm.events.get_closing_voucher_details(frm);
	},
	period_start_date(frm) {
		frm.events.get_closing_voucher_details(frm);
	},
	period_end_date(frm) {
		frm.events.get_closing_voucher_details(frm);
	},

	get_closing_voucher_details(frm) {
		if (frm.doc.company && frm.doc.user && frm.doc.pos_profile) {
			return frappe.call({
				method: "set_closing_voucher_details",
				doc: frm.doc,
				freeze: 1,
				callback: function(r) {
					frm.refresh_fields();
				}
			});
		}
	},

	calculate_cash_denominations(frm) {
		frm.doc.total_cash = 0;
		for (let d of frm.doc.cash_denominations || []) {
			d.amount = flt(d.denomination) * cint(d.count);
			frm.doc.total_cash += d.amount;
		}

		if (frm.doc.total_cash) {
			let row = (frm.doc.payment_reconciliation || []).find(d => d.type == "Cash");
			if (row) {
				frappe.model.set_value(row.doctype, row.name, "closing_amount", frm.doc.total_cash);
			}
		}

		frm.refresh_field("cash_denominations");
		frm.refresh_field("total_cash");
	},

	calculate_totals(frm) {
		frm.doc.total_closing = 0;
		frm.doc.total_difference = 0;

		for (let d of frm.doc.payment_reconciliation || []) {
			d.difference = flt(d.closing_amount) - flt(d.expected_amount)

			frm.doc.total_closing += flt(d.closing_amount);
			frm.doc.total_difference += flt(d.difference);
		}

		frm.refresh_fields();
	},

	make_head_cashier_voucher(frm) {
		return frappe.call({
			method: "erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry.make_head_cashier_voucher",
			args: {
				"pos_closing_entry": frm.doc.name,
			},
			callback: function (r) {
				if (!r.exc) {
					var doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	},

	make_till_transfer_voucher(frm) {
		return frappe.call({
			method: "erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry.make_till_transfer_voucher",
			args: {
				"pos_closing_entry": frm.doc.name,
			},
			callback: function (r) {
				if (!r.exc) {
					var doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				}
			}
		});
	},
});

frappe.ui.form.on('POS Closing Entry Reconciliation', {
	closing_amount: function(frm) {
		frm.events.calculate_totals(frm);
	}
});

frappe.ui.form.on("POS Cash Denomination", {
	count(frm) {
		frm.events.calculate_cash_denominations(frm);
	},

	denomination(frm) {
		frm.events.calculate_cash_denominations(frm);
	},
});
