import frappe


def execute():
	frappe.db.sql("""
		update `tabPOS Opening Entry Detail` d
		inner join `tabMode of Payment` mop on mop.name = d.mode_of_payment
		set d.type = mop.type
	""")
