import frappe


def execute():
	frappe.db.sql("""
		update `tabPayment Entry` set
		paid_amount_before_tax = paid_amount,
		base_paid_amount_before_tax = base_paid_amount,
		paid_amount_after_tax = paid_amount,
		base_paid_amount_after_tax = base_paid_amount,
		received_amount_before_tax = received_amount,
		base_received_amount_before_tax = base_received_amount,
		received_amount_after_tax = received_amount,
		base_received_amount_after_tax = base_received_amount
	""")
