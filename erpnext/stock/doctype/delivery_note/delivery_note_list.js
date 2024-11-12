frappe.listview_settings['Delivery Note'] = {
	add_fields: [
		"customer", "customer_name",
		"billing_status", "is_return", "status",
	],

	get_indicator: function(doc) {
		// Return
		if(cint(doc.is_return)) {
			return [__("Return"), "grey", "is_return,=,Yes|docstatus,=,1"];

		// Closed
		} else if (doc.status === "Closed") {
			return [__("Closed"), "green", "status,=,Closed"];

		// To Bill
		} else if (doc.billing_status == "To Bill") {
			return [__("To Bill"), "orange", "billing_status,=,To Bill|docstatus,=,1"];

		// Completed
		} else if (doc.billing_status != "To Bill") {
			return [__("Completed"), "green", "billing_status,!=,To Bill|docstatus,=,1"];
		}
	},

	onload: function (listview) {
		erpnext.setup_applies_to_listview_filters(listview);

		const action = () => {
			const selected_docs = listview.get_checked_items();
			const docnames = listview.get_checked_items(true);

			if (selected_docs.length > 0) {
				for (let doc of selected_docs) {
					if (!doc.docstatus) {
						frappe.throw(__("Cannot create a Delivery Trip from Draft documents."));
					}
				}

				frappe.new_doc("Delivery Trip")
					.then(() => {
						// Empty out the child table before inserting new ones
						cur_frm.set_value("delivery_stops", []);

						// We don't want to use `map_current_doc` since it brings up
						// the dialog to select more items. We just want the mapper
						// function to be called.
						frappe.call({
							type: "POST",
							method: "frappe.model.mapper.map_docs",
							args: {
								"method": "erpnext.stock.doctype.delivery_note.delivery_note.make_delivery_trip",
								"source_names": docnames,
								"target_doc": cur_frm.doc
							},
							callback: function (r) {
								if (!r.exc) {
									frappe.model.sync(r.message);
									cur_frm.dirty();
									cur_frm.refresh();
								}
							}
						});
					})
			}
		};

		listview.page.add_actions_menu_item(__('Create Delivery Trip'), action, false);
	}
};
