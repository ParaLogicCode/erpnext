// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

// searches for enabled users
frappe.provide("erpnext.queries");
$.extend(erpnext.queries, {
	user: function() {
		return { query: "frappe.core.doctype.user.user.user_query" };
	},

	customer: function() {
		return { query: "erpnext.controllers.queries.customer_query" };
	},

	supplier: function() {
		return { query: "erpnext.controllers.queries.supplier_query" };
	},

	item: function(filters) {
		let args = { query: "erpnext.controllers.queries.item_query" };
		if (filters) {
			args["filters"] = filters;
		}
		return args;
	},

	subcontracted_item: function(purchase_order, filters) {
		if (!filters) {
			filters = {}
		}
		if (purchase_order) {
			filters["purchase_order"] = purchase_order;
		}

		return {
			query: "erpnext.controllers.queries.subcontracted_item_query",
			filters: filters
		};
	},

	item_uom: function(item_code) {
		return {
			query : "erpnext.controllers.queries.item_uom_query",
			filters: {
				item_code: item_code
			}
		}
	},

	bom: function() {
		return { query: "erpnext.controllers.queries.bom" };
	},

	task: function() {
		return { query: "erpnext.projects.utils.query_task" };
	},

	customer_filter: function(doc) {
		if(!doc.customer) {
			frappe.throw(__("Please set {0}", [__(frappe.meta.get_label(doc.doctype, "customer", doc.name))]));
		}

		return { filters: { customer: doc.customer } };
	},

	contact_query: function(doc) {
		return frappe.contacts.contact_query(doc);
	},

	address_query: function(doc) {
		return frappe.contacts.address_query(doc);
	},

	company_address_query: function(doc) {
		return {
			query: 'frappe.contacts.doctype.address.address.address_query',
			filters: {
				link_doctype: 'Company',
				link_name: doc.company || ''
			}
		};
	},

	supplier_filter: function(doc) {
		if(!doc.supplier) {
			frappe.throw(__("Please set {0}", [__(frappe.meta.get_label(doc.doctype, "supplier", doc.name))]));
		}

		return { filters: { supplier: doc.supplier } };
	},

	lead_filter: function(doc) {
		if(!doc.lead) {
			frappe.throw(__("Please specify a {0}",
				[__(frappe.meta.get_label(doc.doctype, "lead", doc.name))]));
		}

		return { filters: { lead: doc.lead } };
	},

	not_a_group_filter: function() {
		return { filters: { is_group: 0 } };
	},

	employee: function() {
		return { query: "erpnext.controllers.queries.employee_query" }
	},

	vehicle_color: function (filters) {
		var args = { query: "erpnext.controllers.queries.vehicle_color_query" };
		if(filters) args["filters"] = filters;
		return args;
	},

	vehicle_interior: function (filters) {
		var args = { query: "erpnext.controllers.queries.vehicle_interior_query" };
		if(filters) args["filters"] = filters;
		return args;
	},

	warehouse: function(doc, get_warehouse_filters) {
		let filters = [
			["Warehouse", "is_group", "=",0]
		];

		if (doc.company) {
			filters.push(["Warehouse", "company", "in", [cstr(doc.company), ""]]);
		}

		if (get_warehouse_filters) {
			get_warehouse_filters(filters);
		}
		return {
			filters: filters
		}
	},

	project_template: function (doc, filters) {
		if (!filters) {
			filters = {};
		}

		if (doc.applies_to_item) {
			filters['applies_to_item'] = doc.applies_to_item;
		}

		if (doc.applies_to_item_group) {
			filters['applies_to_item_group'] = doc.applies_to_item_group;
		}

		if (doc.applies_to_vehicle_region) {
			filters['applies_to_vehicle_region'] = doc.applies_to_vehicle_region;
		}

		return {
			query: "erpnext.controllers.queries.project_template_query",
			filters: filters,
		};
	},

	workstation: function (operation) {
		return {
			query: "erpnext.controllers.queries.workstation_query",
			filters: {
				operation: operation
			},
		};
	},
});

erpnext.queries.setup_queries = function(frm, options, query_fn, do_not_override) {
	let set_query = function(doctype, parentfield) {
		let link_fields = frappe.meta.get_docfields(doctype, frm.doc.name, {"fieldtype": "Link", "options": options});
		$.each(link_fields, function(i, df) {
			if (parentfield) {
				frm.set_query(df.fieldname, parentfield, query_fn.bind(frm.cscript, df.fieldname));
			} else {
				frm.set_query(df.fieldname, query_fn.bind(frm.cscript, df.fieldname));
			}
		});
	};

	set_query(frm.doc.doctype);

	// warehouse field in tables
	$.each(frappe.meta.get_docfields(frm.doc.doctype, frm.doc.name, {"fieldtype": "Table"}), function(i, df) {
		set_query(df.options, df.fieldname);
	});
}

/* 	if item code is selected in child table
	then list down warehouses with its quantity
	else apply default filters.
*/
erpnext.queries.setup_warehouse_qty_query = function(frm, warehouse_field, items_field) {
	warehouse_field = warehouse_field || "warehouse";
	items_field = items_field || "items";

	frm.set_query(warehouse_field, items_field, function(doc, cdt, cdn) {
		let warehouse_query = erpnext.queries.warehouse(frm.doc,
			frm.cscript.get_warehouse_filters && frm.cscript.get_warehouse_filters.bind(frm.cscript, warehouse_field));

		let row = frappe.get_doc(cdt, cdn);
		if (row && row.item_code) {
			warehouse_query["query"] = "erpnext.controllers.queries.warehouse_query";
			warehouse_query["filters"].push(["Bin", "item_code", "=", row.item_code]);
		}

		return warehouse_query;
	});
}
