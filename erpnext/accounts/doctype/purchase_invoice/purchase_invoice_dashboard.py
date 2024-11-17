import frappe
from frappe import _


def get_data():
	return {
		'fieldname': 'purchase_invoice',
		'non_standard_fieldnames': {
			'Journal Entry': 'reference_name',
			'Payment Entry': 'reference_name',
			'Payment Request': 'reference_name',
			'Landed Cost Voucher': 'receipt_document',
			'Purchase Invoice': 'return_against',
			'Auto Repeat': 'reference_document'
		},
		'internal_links': {
			'Purchase Order': ['items', 'purchase_order'],
			'Purchase Receipt': ['items', 'purchase_receipt'],
			'Work Order': ['items', 'work_order'],
		},
		'transactions': [
			{
				'label': _('Payment'),
				'items': ['Payment Entry', 'Journal Entry', 'Expense Claim']
			},
			{
				'label': _('Previous Document'),
				'items': ['Purchase Order', 'Purchase Receipt', 'Work Order']
			},
			{
				'label': _('Reference'),
				'items': ['Asset', 'Landed Cost Voucher']
			},
			{
				'label': _('Returns'),
				'items': ['Purchase Invoice']
			},
		]
	}
