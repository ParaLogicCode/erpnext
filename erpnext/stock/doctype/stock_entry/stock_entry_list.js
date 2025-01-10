frappe.listview_settings['Stock Entry'] = {
	add_fields: ["purpose", "transfer_status"],
	get_indicator: function (doc) {
		if (doc.purpose == 'Send to Warehouse' && doc.transfer_status == "In Transit") {
			return [__("In Transit"), "orange", "transfer_status,=,In Transit"];
		} else if (doc.purpose == 'Send to Warehouse' && doc.transfer_status === "Transferred") {
			return [__("Transferred"), "green", "transfer_status,=,Transferred"];
		} else if (doc.purpose == "Material Issue") {
			return [__("Issued"), "blue"];
		} else if (["Material Receipt", "Receive at Warehouse"].includes(doc.purpose)) {
			return [__("Received"), "blue"];
		} else if (["Material Transfer", "Material Transfer for Manufacture"].includes(doc.purpose)) {
			return [__("Transferred"), "blue"];
		} else if (doc.purpose == "Material Consumption for Manufacture") {
			return [__("Consumed"), "blue"];
		} else if (doc.purpose == "Manufacture") {
			return [__("Produced"), "blue"];
		} else if (doc.purpose == "Send to Subcontractor") {
			return [__("Sent"), "blue"];
		} else {
			return [__("Submitted"), "blue", "docstatus,=,1"];
		}
	},
};
