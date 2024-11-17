from frappe import _


def get_data():
	return {
		'heatmap': True,
		'heatmap_message': _('This is based on stock movement. See {0} for details')\
			.format('<a href="/app/query-report/Stock Ledger">' + _('Stock Ledger') + '</a>'),
		'fieldname': 'item_code',
		'non_standard_fieldnames': {
			'Work Order': 'production_item',
			'Product Bundle': 'new_item_code',
			'BOM': 'item',
			'Batch': 'item',
			'Project': 'applies_to_item'
		},
		'transactions': [
			{
				'label': _('Sales'),
				'items': ['Quotation', 'Sales Order', 'Delivery Note', 'Sales Invoice']
			},
			{
				'label': _('Purchase'),
				'items': ['Purchase Order', 'Purchase Receipt', 'Purchase Invoice', 'Landed Cost Voucher']
			},
			{
				'label': _('Stock'),
				'items': ['Stock Entry', 'Stock Reconciliation', 'Packing Slip']
			},
			{
				'label': _('Request'),
				'items': ['Material Request', 'Supplier Quotation', 'Request for Quotation', 'Project']
			},
			{
				'label': _('Configuration'),
				'items': ['BOM', 'Item Alternative', 'Item Default Rule']
			},
			{
				'label': _('Pricing'),
				'items': ['Item Price', 'Pricing Rule']
			},
			{
				'label': _('Traceability'),
				'items': ['Serial No', 'Batch']
			},
			{
				'label': _('Manufacture'),
				'items': ['Production Plan', 'Work Order', 'Item Manufacturer']
			},
		]
	}
