frappe.listview_settings['Service Warranty'] = {
	add_fields: ["status"],
	get_indicator: (doc) => {
		if (doc.status == "Active") {
			return [__("Active"), "green", "status,=,Active"];
		} else if (doc.status == "Expired") {
			return [__("Expired"), "gray", "status,=,Expired"];
		}
	}
};
