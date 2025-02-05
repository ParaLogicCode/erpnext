import frappe


def execute():
	frappe.db.sql("""
		update `tabExpense Entry Detail` d
		inner join `tabSupplier` s on s.name = d.supplier
		set d.supplier_name = s.supplier_name
	""")
