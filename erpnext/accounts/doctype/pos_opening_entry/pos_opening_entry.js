// Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Opening Entry", {
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
			if (!frm.doc.period_start_date) {
				frm.set_value("period_start_date", frappe.datetime.now_datetime());
			}
			if (!frm.doc.period_start_time) {
				frm.set_value("period_start_time", frappe.datetime.now_time());
			}

			if (!frm.doc.user) {
				frm.set_value("user", frappe.session.user);
			}

			if (!frm.doc.cash_denominations?.length) {
				frm.events.get_cash_denominations(frm);
			}
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
						frm.set_value("pos_profile", r.message);
					}
				}
			});
		}
	},

	pos_profile: (frm) => {
		frm.events.get_pos_profile_details(frm);
	},

	get_pos_profile_details(frm) {
		if (frm.doc.pos_profile) {
			return frappe.call({
				method: "erpnext.accounts.doctype.pos_opening_entry.pos_opening_entry.get_pos_profile_details",
				args: {
					pos_profile: frm.doc.pos_profile,
				},
				callback: (r) => {
					if (r.message) {
						frm.set_value(r.message);
					}
				}
			});
		}
	},

	get_cash_denominations(frm) {
		return frappe.call({
			method: "erpnext.accounts.doctype.pos_profile.pos_profile.get_cash_denominations",
			callback: (r) => {
				if (r.message) {
					frm.set_value("cash_denominations", r.message);
				}
			}
		});
	},

	calculate_cash_denominations(frm) {
		frm.doc.total_cash = 0;
		for (let d of frm.doc.cash_denominations || []) {
			d.amount = flt(d.denomination) * cint(d.count);
			frm.doc.total_cash += d.amount;
		}

		if (frm.doc.total_cash) {
			let row = (frm.doc.balance_details || []).find(d => d.type == "Cash");
			if (row) {
				frappe.model.set_value(row.doctype, row.name, "opening_amount", frm.doc.total_cash);
			}
		}

		frm.refresh_field("cash_denominations");
		frm.refresh_field("total_cash");
	},
});

frappe.ui.form.on("POS Cash Denomination", {
	count(frm) {
		frm.events.calculate_cash_denominations(frm);
	},

	denomination(frm) {
		frm.events.calculate_cash_denominations(frm);
	},
});
