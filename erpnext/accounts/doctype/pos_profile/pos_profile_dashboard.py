
from frappe import _


def get_data():
	return {
		'fieldname': 'pos_profile',
		'transactions': [
			{
				'label': _('Opening & Closing'),
				'items': ['POS Opening Entry', 'POS Closing Entry']
			},
			{
				'label': _('Invoices'),
				'items': ['Sales Invoice']
			},
		]
	}
