import frappe


def execute():
	frappe.db.sql("""
		update `tabStock Ledger Entry` sle
		inner join `tabStock Entry` p on sle.voucher_no = p.name and sle.voucher_type = 'Stock Entry'
		inner join `tabStock Entry Detail` i on i.name = sle.voucher_detail_no and i.parent = p.name
		set sle.is_transfer = 1
		where ifnull(i.s_warehouse, '') != '' and ifnull(i.t_warehouse, '') != ''
	""")

	frappe.db.sql("""
		update `tabStock Ledger Entry` sle
		inner join `tabPacking Slip` p on sle.voucher_no = p.name and sle.voucher_type = 'Packing Slip'
		inner join `tabPacking Slip Item` i on i.name = sle.voucher_detail_no and i.parent = p.name
		set sle.is_transfer = 1
	""")
