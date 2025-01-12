import frappe


def execute():
	frappe.db.sql("""
		update `tabProject Service Template` pst
		inner join `tabSales Order` so on so.project = pst.parent
		inner join `tabSales Order Item` i on i.parent = so.name and i.service_template_detail = pst.name
		set pst.has_sales_order = 1
		where so.docstatus = 1
	""")

	frappe.db.sql("""
		update `tabProject Service Template` pst
		inner join `tabMaterial Request` mreq on mreq.project = pst.parent
		inner join `tabMaterial Request Item` i on i.parent = mreq.name and i.service_template_detail = pst.name
		set pst.has_material_request = 1
		where mreq.docstatus = 1
	""")
