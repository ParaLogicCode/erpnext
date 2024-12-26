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
});
