// Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Service Warranty", {
	setup(frm) {
		erpnext.setup_applies_to_fields(frm);
		frm.events.setup_queries(frm);
	},

	refresh(frm) {
		frm.events.setup_buttons(frm);
	},

	setup_queries(frm) {
		frm.set_query("service_template", () => {
			return { filters: { includes_service_warranty: 1 } }
		});

		frm.set_query("warranty_provision_account", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					root_type: "Liability",
				}
			}
		});

		frm.set_query("warranty_expense_account", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
					root_type: "Expense",
				}
			}
		});

		frm.set_query("cost_center", () => {
			return {
				filters: {
					company: frm.doc.company,
					is_group: 0,
				}
			}
		});
	},

	setup_buttons(frm) {
		if (frm.doc.docstatus == 1) {
			frm.add_custom_button(__('Accounting Ledger'), () => {
				frappe.route_options = {
					"voucher_no": frm.doc.name,
					"from_date": frm.doc.posting_date,
					"to_date": frm.doc.posting_date,
					"company": frm.doc.company,
					"merge_similar_entries": 0
				};
				frappe.set_route("query-report", "General Ledger");
			}, __('View'));
		}
	},

	project(frm) {
		frm.events.get_project_details(frm);
	},

	get_project_details(frm) {
		if (frm.doc.project) {
			return frappe.call({
				method: 'erpnext.projects.doctype.project.project.get_project_details',
				args: {
					project: frm.doc.project,
					doctype: frm.doc.doctype,
				},
				callback: (r) => {
					if (r.message) {
						return frm.set_value(r.message);
					}
				}
			});
		}
	},

	valid_from(frm) {
		frm.events.set_valid_upto(frm);
	},

	warranty_validity(frm) {
		frm.events.set_valid_upto(frm);
	},

	set_valid_upto(frm) {
		if (!frm.doc.valid_from || cint(frm.doc.warranty_validity) <= 0) {
			return;
		}

		let valid_upto = frappe.datetime.add_months(frm.doc.valid_from, cint(frm.doc.warranty_validity));
		valid_upto = frappe.datetime.add_days(valid_upto,-1);

		frm.set_value("valid_upto", valid_upto);
	},

	tc_name(frm) {
		if (frm.doc.tc_name) {
			erpnext.utils.get_terms(frm.doc.tc_name, frm.doc, function(r) {
				frm.set_value("terms", r.message);
			});
		} else {
			frm.set_value("terms", null);
		}
	}
});
