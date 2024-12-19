frappe.listview_settings['Project'] = {
	add_fields: ["project_status", "status", "indicator_color", "priority", "is_active", "percent_complete", "project_name"],
	get_indicator: function(doc) {
		let color_map = {
			"Open": "orange",
			"Completed": "green",
			"Closed": "green",
			"Cancelled": "light-gray",
		}

		let guessed_color = color_map[doc.status] || frappe.utils.guess_colour(doc.status);

		if (doc.project_status) {
			return [
				__(doc.project_status),
				doc.indicator_color || guessed_color,
				"project_status,=," + doc.project_status
			];
		} else {
			return [
				__(doc.status) + percentage,
				guessed_color,
				"status,=," + doc.status
			];
		}
	},

	onload: function(listview) {
		erpnext.setup_applies_to_listview_filters(listview);

		if (listview.page.fields_dict.customer) {
			listview.page.fields_dict.customer.get_query = () => {
				return erpnext.queries.customer();
			}
		}
		if (listview.page.fields_dict.bill_to) {
			listview.page.fields_dict.bill_to.get_query = () => {
				return erpnext.queries.customer();
			}
		}
	}
};
