import frappe


def get_data():
	return {
		'fieldname': 'vehicle',
		'non_standard_fieldnames': {
			'Maintenance Schedule': 'serial_no'
		},
		'transactions': [
			{
				'label': ['Reference'],
				'items': ['Vehicle Log', 'Maintenance Schedule']
			},
		]
	}
