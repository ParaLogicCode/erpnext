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
		frm.refresh_field("cash_denominations");
		frm.refresh_field("total_cash");
	},
});

frappe.ui.form.on('POS Closing Entry Detail', {
	closing_amount: function(doc, cdt, cdn) {
		var row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "difference", flt(row.closing_amount) - flt(row.expected_amount));
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
