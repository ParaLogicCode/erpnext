from frappe import _


def get_data():
	return {
		'heatmap': False,
		'heatmap_message': _('This is based on the Time Sheets created against this project'),
		'fieldname': 'project',
		'transactions': [
			{
				'label': _('Sales'),
				'items': ['Quotation', 'Sales Order', 'Sales Invoice']
			},
			{
				'label': _('Material'),
				'items': ['Material Request', 'Stock Entry', 'Delivery Note']
			},
			{
				'label': _('Tasks'),
				'items': ['Task', 'Issue', 'Timesheet']
			},
			{
				'label': _('Purchase'),
				'items': ['Purchase Order', 'Purchase Receipt', 'Purchase Invoice']
			},
			{
				'label': _('Accounting'),
				'items': ['Journal Entry', 'Payment Entry', 'Service Warranty']
			},
			{
				'label': _('Expenses'),
				'items': ['Employee Advance', 'Expense Claim']
			},
		]
	}
