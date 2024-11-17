import frappe

def execute():
	frappe.reload_doc('erpnext_integrations', 'doctype', 'shopify_settings')
	frappe.db.set_single_value('Shopify Settings', 'app_type', 'Private')