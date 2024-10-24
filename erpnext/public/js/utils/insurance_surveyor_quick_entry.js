frappe.provide('frappe.ui.form');

frappe.ui.form.InsuranceSurveyorQuickEntryForm = class InsuranceSurveyorQuickEntryForm extends frappe.ui.form.QuickEntryForm {
	init(doctype, after_insert) {
		super.init(doctype, after_insert);
	}

	render_dialog() {
		super.render_dialog();
		if (this.dialog.get_field("insurance_company")) {
			this.dialog.get_field("insurance_company").get_query = function () {
				return {
					query: "erpnext.controllers.queries.customer_query",
					filters: {'is_insurance_company': 1}
				}
			}
		}

		if (this.dialog.get_field("insurance_surveyor_mobile_no")) {
			this.dialog.fields_dict["insurance_surveyor_mobile_no"].df.onchange = () => {
				let value = this.dialog.get_value('insurance_surveyor_mobile_no');
				value = frappe.regional.pakistan.get_formatted_mobile_no(value);
				this.dialog.doc.insurance_surveyor_mobile_no = value;
				this.dialog.get_field('insurance_surveyor_mobile_no').refresh();
			};
		}
	}
};