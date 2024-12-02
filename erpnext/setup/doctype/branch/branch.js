// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Branch', {
	refresh: function(frm) {
		frm.toggle_display('address_html', !frm.doc.__islocal);
		if(!frm.doc.__islocal) {
			frappe.dynamic_link = {doc: frm.doc, fieldname: 'name', doctype: 'Branch'}
			frappe.contacts.render_address_and_contact(frm);
		}
	}
});
