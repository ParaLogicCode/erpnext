// Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Insurance Surveyor', {
	setup: function(frm) {
		frm.set_query("insurance_company", function () {
			return {
				query: "erpnext.controllers.queries.customer_query",
				filters: {
					'is_insurance_company': 1
				}
			}
		})
	},

	validate: function(frm) {
		frappe.regional.pakistan.format_mobile_no(frm, "insurance_surveyor_mobile_no");
	},

	insurance_surveyor_mobile_no: function (frm) {
		frappe.regional.pakistan.format_mobile_no(frm, "insurance_surveyor_mobile_no");
	},

});
