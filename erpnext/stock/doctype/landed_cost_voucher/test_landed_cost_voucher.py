# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import unittest
import frappe
from frappe.utils import flt
from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt \
	import set_perpetual_inventory, get_gl_entries, test_records as pr_test_records, make_purchase_receipt
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.accounts.doctype.account.test_account import get_inventory_account

class TestLandedCostVoucher(unittest.TestCase):
	def test_landed_cost_voucher(self):
		frappe.db.set_value("Buying Settings", None, "allow_multiple_items", 1)

		pr = make_purchase_receipt(company="_Test Company with perpetual inventory", warehouse = "Stores - TCP1", supplier_warehouse = "Work in Progress - TCP1", get_multiple_items = True, get_taxes_and_charges = True)


		last_sle = frappe.db.get_value("Stock Ledger Entry", {
				"voucher_type": pr.doctype,
				"voucher_no": pr.name,
				"item_code": "_Test Item",
				"warehouse": "Stores - TCP1"
			},
			fieldname=["qty_after_transaction", "stock_value"], as_dict=1)

		submit_landed_cost_voucher("Purchase Receipt", pr.name)

		pr_lc_value = frappe.db.get_value("Purchase Receipt Item", {"parent": pr.name}, "landed_cost_voucher_amount")
		self.assertEqual(pr_lc_value, 25.0)

		last_sle_after_landed_cost = frappe.db.get_value("Stock Ledger Entry", {
				"voucher_type": pr.doctype,
				"voucher_no": pr.name,
				"item_code": "_Test Item",
				"warehouse": "Stores - TCP1"
			},
			fieldname=["qty_after_transaction", "stock_value"], as_dict=1)

		self.assertEqual(last_sle.qty_after_transaction, last_sle_after_landed_cost.qty_after_transaction)

		self.assertEqual(last_sle_after_landed_cost.stock_value - last_sle.stock_value, 25.0)

		gl_entries = get_gl_entries("Purchase Receipt", pr.name)

		self.assertTrue(gl_entries)

		stock_in_hand_account = get_inventory_account(pr.company, pr.get("items")[0].warehouse)
		fixed_asset_account = get_inventory_account(pr.company, pr.get("items")[1].warehouse)

		if stock_in_hand_account == fixed_asset_account:
			expected_values = {
				stock_in_hand_account: [800.0, 0.0],
				"Stock Received But Not Billed - TCP1": [0.0, 500.0],
				"Expenses Included In Valuation - TCP1": [0.0, 50.0],
				"_Test Account Customs Duty - TCP1": [0.0, 150],
				"_Test Account Shipping Charges - TCP1": [0.0, 100.00]
			}
		else:
			expected_values = {
				stock_in_hand_account: [400.0, 0.0],
				fixed_asset_account: [400.0, 0.0],
				"Stock Received But Not Billed - TCP1": [0.0, 500.0],
				"Expenses Included In Valuation - TCP1": [0.0, 300.0]
			}

		for gle in gl_entries:
			self.assertEqual(expected_values[gle.account][0], gle.debit)
			self.assertEqual(expected_values[gle.account][1], gle.credit)


	def test_landed_cost_voucher_against_purchase_invoice(self):

		pi = make_purchase_invoice(update_stock=1, posting_date=frappe.utils.nowdate(),
			posting_time=frappe.utils.nowtime(), cash_bank_account="Cash - TCP1",
			company="_Test Company with perpetual inventory", supplier_warehouse="Work In Progress - TCP1",
			warehouse= "Stores - TCP1", cost_center = "Main - TCP1",
			expense_account ="_Test Account Cost for Goods Sold - TCP1")

		last_sle = frappe.db.get_value("Stock Ledger Entry", {
				"voucher_type": pi.doctype,
				"voucher_no": pi.name,
				"item_code": "_Test Item",
				"warehouse": "Stores - TCP1"
			},
			fieldname=["qty_after_transaction", "stock_value"], as_dict=1)

		submit_landed_cost_voucher("Purchase Invoice", pi.name)

		pi_lc_value = frappe.db.get_value("Purchase Invoice Item", {"parent": pi.name},
			"landed_cost_voucher_amount")

		self.assertEqual(pi_lc_value, 50.0)

		last_sle_after_landed_cost = frappe.db.get_value("Stock Ledger Entry", {
				"voucher_type": pi.doctype,
				"voucher_no": pi.name,
				"item_code": "_Test Item",
				"warehouse": "Stores - TCP1"
			},
			fieldname=["qty_after_transaction", "stock_value"], as_dict=1)

		self.assertEqual(last_sle.qty_after_transaction, last_sle_after_landed_cost.qty_after_transaction)

		self.assertEqual(last_sle_after_landed_cost.stock_value - last_sle.stock_value, 50.0)

		gl_entries = get_gl_entries("Purchase Invoice", pi.name)

		self.assertTrue(gl_entries)
		stock_in_hand_account = get_inventory_account(pi.company, pi.get("items")[0].warehouse)

		expected_values = {
			stock_in_hand_account: [300.0, 0.0],
			"Creditors - TCP1": [0.0, 250.0],
			"Expenses Included In Valuation - TCP1": [0.0, 50.0]
		}

		for gle in gl_entries:
			self.assertEqual(expected_values[gle.account][0], gle.debit)
			self.assertEqual(expected_values[gle.account][1], gle.credit)


	def test_landed_cost_voucher_for_serialized_item(self):
		frappe.db.sql("delete from `tabSerial No` where name in ('SN001', 'SN002', 'SN003', 'SN004', 'SN005')")
		pr = make_purchase_receipt(company="_Test Company with perpetual inventory", warehouse = "Stores - TCP1",
		supplier_warehouse = "Work in Progress - TCP1", get_multiple_items = True,
		get_taxes_and_charges = True, do_not_submit = True)

		pr.items[0].item_code = "_Test Serialized Item"
		pr.items[0].serial_no = "SN001\nSN002\nSN003\nSN004\nSN005"
		pr.submit()

		serial_no_rate = frappe.db.get_value("Serial No", "SN001", "purchase_rate")

		submit_landed_cost_voucher("Purchase Receipt", pr.name)

		serial_no = frappe.db.get_value("Serial No", "SN001",
			["warehouse", "purchase_rate"], as_dict=1)

		self.assertEqual(serial_no.purchase_rate - serial_no_rate, 5.0)
		self.assertEqual(serial_no.warehouse, "Stores - TCP1")


	def test_landed_cost_voucher_for_odd_numbers (self):

		pr = make_purchase_receipt(company="_Test Company with perpetual inventory", warehouse = "Stores - TCP1", supplier_warehouse = "Work in Progress - TCP1", do_not_save=True)
		pr.items[0].cost_center = "Main - TCP1"
		for x in range(2):
			pr.append("items", {
				"item_code": "_Test Item",
				"warehouse": "Stores - TCP1",
				"cost_center": "Main - TCP1",
				"qty": 5,
				"rate": 50
			})
		pr.submit()

		lcv = submit_landed_cost_voucher("Purchase Receipt", pr.name, 123.22)

		self.assertEqual(lcv.items[0].applicable_charges, 41.07)
		self.assertEqual(lcv.items[2].applicable_charges, 41.08)

	def test_multiple_landed_cost_voucher_against_pr(self):
		pr = make_purchase_receipt(company="_Test Company with perpetual inventory", warehouse = "Stores - TCP1", 
			supplier_warehouse = "Stores - TCP1", do_not_save=True)

		pr.append("items", {
			"item_code": "_Test Item",
			"warehouse": "Stores - TCP1",
			"cost_center": "Main - TCP1",
			"qty": 5,
			"rate": 100
		})

		pr.submit()

		lcv1 = make_landed_cost_voucher(receipt_document_type = 'Purchase Receipt', 
			receipt_document=pr.name, charges=100, do_not_save=True)

		lcv1.insert()
		lcv1.set('items', [
			lcv1.get('items')[0]
		])
		distribute_landed_cost_on_items(lcv1)

		lcv1.submit()

		lcv2 = make_landed_cost_voucher(receipt_document_type = 'Purchase Receipt', 
			receipt_document=pr.name, charges=100, do_not_save=True)

		lcv2.insert()
		lcv2.set('items', [
			lcv2.get('items')[1]
		])
		distribute_landed_cost_on_items(lcv2)

		lcv2.submit()

		pr.load_from_db()

		self.assertEqual(pr.items[0].landed_cost_voucher_amount, 100)
		self.assertEqual(pr.items[1].landed_cost_voucher_amount, 100)

def make_landed_cost_voucher(** args):
	args = frappe._dict(args)
	ref_doc = frappe.get_doc(args.receipt_document_type, args.receipt_document)

	lcv = frappe.new_doc('Landed Cost Voucher')
	lcv.company = '_Test Company'
	lcv.distribute_charges_based_on = 'Amount'

	lcv.set('purchase_receipts', [{
		"receipt_document_type": args.receipt_document_type,
		"receipt_document": args.receipt_document,
		"supplier": ref_doc.supplier,
		"posting_date": ref_doc.posting_date,
		"grand_total": ref_doc.grand_total
	}])

	lcv.set("taxes", [{
		"description": "Shipping Charges",
		"expense_account": "Expenses Included In Valuation - TCP1",
		"amount": args.charges
	}])

	if not args.do_not_save:
		lcv.insert()
		if not args.do_not_submit:
			lcv.submit()

	return lcv


def submit_landed_cost_voucher(receipt_document_type, receipt_document, charges=50):
	ref_doc = frappe.get_doc(receipt_document_type, receipt_document)

	lcv = frappe.new_doc("Landed Cost Voucher")
	lcv.company = "_Test Company"
	lcv.distribute_charges_based_on = 'Amount'

	lcv.set("purchase_receipts", [{
		"receipt_document_type": receipt_document_type,
		"receipt_document": receipt_document,
		"supplier": ref_doc.supplier,
		"posting_date": ref_doc.posting_date,
		"grand_total": ref_doc.base_grand_total
	}])

	lcv.set("taxes", [{
		"description": "Insurance Charges",
		"expense_account": "Expenses Included In Valuation - TCP1",
		"amount": charges
	}])

	lcv.insert()

	distribute_landed_cost_on_items(lcv)

	lcv.submit()

	return lcv

def distribute_landed_cost_on_items(lcv):
	based_on = lcv.distribute_charges_based_on.lower()
	total = sum([flt(d.get(based_on)) for d in lcv.get("items")])

	for item in lcv.get("items"):
		item.applicable_charges = flt(item.get(based_on)) * flt(lcv.total_taxes_and_charges) / flt(total)
		item.applicable_charges = flt(item.applicable_charges, lcv.precision("applicable_charges", item))

test_records = frappe.get_test_records('Landed Cost Voucher')
