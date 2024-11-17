// Common UI functionality for applies_to fields

frappe.provide("erpnext");

erpnext.setup_applies_to_fields_hooks = [];
erpnext.update_applies_to_details_args_hooks = [];

erpnext.setup_applies_to_fields = function (frm) {
	frappe.ui.form.on(frm.doctype, {
		applies_to_item: function(frm) {
			frm.events.get_applies_to_details(frm);
		},

		applies_to_serial_no: function(frm) {
			frm.events.get_applies_to_details(frm);
		},

		get_applies_to_details: function(frm) {
			if (frm._in_get_applies_to_details) {
				return;
			}
			frm._in_get_applies_to_details = true;

			let args =  {
				applies_to_item: frm.doc.applies_to_item,
				applies_to_serial_no: frm.doc.applies_to_serial_no,
				doctype: frm.doc.doctype,
				name: frm.doc.name,
			};

			if (frm.doc.doctype != "Project") {
				args.project = frm.doc.project;
			}

			for (let func of erpnext.update_applies_to_details_args_hooks || []) {
				func(frm, args);
			}

			return frappe.call({
				method: "erpnext.stock.get_item_details.get_applies_to_details",
				args: {
					args: args
				},
				callback: (r) => {
					return frappe.run_serially([
						() => {
							if (r.message) {
								return frm.set_value(r.message);
							}
						},
						() => frm._in_get_applies_to_details = false,
					]);
				},
				error: () => {
					frm._in_get_applies_to_details = false;
				}
			})
		},
	});

	for (let func of erpnext.setup_applies_to_fields_hooks || []) {
		func(frm);
	}
}

erpnext.setup_applies_to_listview_filters = function (listview) {
	if (listview.page.fields_dict.applies_to_variant_of) {
		listview.page.fields_dict.applies_to_variant_of.get_query = () => {
			return erpnext.queries.item({"has_variants": 1, "include_disabled": 1});
		}
	}

	if (listview.page.fields_dict.applies_to_item) {
		listview.page.fields_dict.applies_to_item.get_query = () => {
			let filters = {"include_disabled": 1};

			let variant_of = listview.page.fields_dict.applies_to_variant_of?.get_value('applies_to_variant_of');
			if (variant_of) {
				filters['variant_of'] = variant_of;
			}

			return erpnext.queries.item(filters);
		}
	}
}
