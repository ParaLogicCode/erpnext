frappe.listview_settings['Material Request'] = {
	add_fields: ["material_request_type", "status", "per_ordered", "per_received"],

	get_indicator: function(doc) {
		let color_map = {
			"Stopped": "red",
			"Pending": "orange",
			"Received": "green",
			"Partially Received": "yellow",
			"Ordered": "blue",
			"Partially Ordered": "yellow",
		}

		return [__(doc.status), color_map[doc.status] || "gray", "status,=," + doc.status];
	},

	onload: function(listview) {
		erpnext.setup_applies_to_listview_filters(listview);
	},
};
