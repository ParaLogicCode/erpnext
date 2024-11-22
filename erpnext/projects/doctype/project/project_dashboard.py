from frappe import _


def get_data():
	return {
		'heatmap': False,
		'heatmap_message': _('This is based on the Time Sheets created against this project'),
		'fieldname': 'project',
		'transactions': [
			{
				'label': _('Sales'),
				'items': ['Quotation', 'Sales Order', 'Delivery Note', 'Sales Invoice']
			},
			{
				'label': _('Purchase'),
				'items': ['Supplier Quotation', 'Purchase Order', 'Purchase Receipt', 'Purchase Invoice']
			},
			{
				'label': _('Tasks'),
				'items': ['Task', 'Issue', 'Timesheet']
			},
			{
				'label': _('Material'),
				'items': ['Stock Entry', 'Material Request']
			},
			{
				'label': _('Expenses'),
				'items': ['Employee Advance', 'Expense Claim']
			},
			{
				'label': _('Accounting'),
				'items': ['Journal Entry', 'Payment Entry']
			},
		]
	}
