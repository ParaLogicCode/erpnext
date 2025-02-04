# import frappe
from frappe import _


def get_data():
	return {
		'fieldname': 'pos_opening_entry',
		'transactions': [
			{
				'label': _('Closing'),
				'items': ['POS Closing Entry']
			},
		]
	}
