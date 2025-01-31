from frappe import _


def get_data():
	return {
		'fieldname': 'service_template_category',
		'transactions': [
			{
				'label': _("Service Templates"),
				'items': ['Service Template']
			}
		]
	}
