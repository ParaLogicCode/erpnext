from frappe import _

def get_data():
	return {
		'fieldname': 'service_template',
		'transactions': [
			{
				'label': _("Project"),
				'items': ['Project']
			}
		]
	}
