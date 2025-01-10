# import frappe
from frappe import _


def get_data():
	return {
		'fieldname': 'stock_entry',
		'internal_links': {
			'Material Request': ['items', 'material_request'],
			'Packing Slip': ['items', 'packing_slip'],
		},
		'non_standard_fieldnames': {
			'Stock Entry': 'against_stock_entry',
		},
		'transactions': [
			{
				'label': _('Previous Documents'),
				'items': ['Material Request']
			},
			{
				'label': _('Packing'),
				'items': ['Packing Slip']
			},
			{
				'label': _('Transfer'),
				'items': ['Stock Entry']
			},
		]
	}
