import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	frappe.reload_doctype("Project")
	if frappe.db.has_column("Project", "stock_sales_amount"):
		rename_field("Project", "stock_sales_amount", "material_sales_amount")
