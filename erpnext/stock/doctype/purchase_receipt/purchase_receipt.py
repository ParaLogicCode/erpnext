# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate, getdate
from erpnext.controllers.buying_controller import BuyingController
from erpnext.accounts.utils import get_account_currency
from frappe.desk.notifications import clear_doctype_notifications
from frappe.model.mapper import get_mapped_doc
from erpnext.buying.utils import check_on_hold_or_closed_status
from erpnext.assets.doctype.asset.asset import get_asset_account, is_cwip_accounting_enabled
from erpnext.assets.doctype.asset_category.asset_category import get_asset_category_account
from erpnext.stock import get_warehouse_account_map


form_grid_templates = {
	"items": "templates/form_grid/item_grid.html"
}


class PurchaseReceipt(BuyingController):
	def __init__(self, *args, **kwargs):
		super(PurchaseReceipt, self).__init__(*args, **kwargs)
		self.status_map = [
			["Draft", None],
			["To Bill", "eval:self.billing_status == 'To Bill' and self.docstatus == 1"],
			["Completed", "eval:self.billing_status != 'To Bill' and self.docstatus == 1"],
			["Return", "eval:self.is_return and self.docstatus == 1"],
			["Closed", "eval:self.status == 'Closed'"],
			["Cancelled", "eval:self.docstatus == 2"],
		]

	def validate(self):
		self.validate_posting_time()
		super(PurchaseReceipt, self).validate()

		self.validate_order_required()
		self.validate_with_previous_doc()
		self.validate_uom_is_integer("uom", ["qty", "received_qty"])
		self.validate_uom_is_integer("stock_uom", "stock_qty")
		self.validate_cwip_accounts()

		self.check_on_hold_or_closed_status()

		from erpnext.accounts.doctype.sales_invoice.sales_invoice import validate_inter_company_party
		validate_inter_company_party(self.doctype, self.supplier, self.company, self.inter_company_reference)

		if getdate(self.posting_date) > getdate(nowdate()):
			frappe.throw(_("Posting Date cannot be future date"))

		self.set_billing_status()
		self.set_status()

		self.set_title()

	def on_submit(self):
		if not self.is_return:
			self.auto_create_batches('warehouse')

		super(PurchaseReceipt, self).on_submit()

		# Check for Approving Authority
		frappe.get_doc('Authorization Control').validate_approving_authority(self.doctype, self.company, self.base_grand_total)

		self.validate_previous_docstatus()
		self.update_previous_doc_status()

		from erpnext.accounts.doctype.sales_invoice.sales_invoice import update_linked_doc
		update_linked_doc(self.doctype, self.name, self.inter_company_reference)

		# Updating stock ledger should always be called after updating prevdoc status,
		# because updating ordered qty, reserved_qty_for_subcontract in bin
		# depends upon updated ordered qty in PO
		self.update_stock_ledger()

		from erpnext.stock.doctype.serial_no.serial_no import update_serial_nos_after_submit
		update_serial_nos_after_submit(self, "items")

		self.make_gl_entries()

	def on_cancel(self):
		super(PurchaseReceipt, self).on_cancel()
		self.update_status_on_cancel()

		# Check if Purchase Invoice has been submitted against current Purchase Order
		submitted = frappe.db.sql("""
			select t1.name
			from `tabPurchase Invoice` t1,`tabPurchase Invoice Item` t2
			where t1.name = t2.parent and t2.purchase_receipt = %s and t1.docstatus = 1
		""", self.name)
		if submitted:
			frappe.throw(_("Purchase Invoice {0} is already submitted").format(submitted[0][0]))

		self.update_previous_doc_status()

		# Updating stock ledger should always be called after updating prevdoc status,
		# because updating ordered qty in bin depends upon updated ordered qty in PO
		self.update_stock_ledger()
		self.make_gl_entries_on_cancel()
		self.unlink_auto_created_batches()

		if not self.is_return:
			from erpnext.accounts.doctype.sales_invoice.sales_invoice import unlink_inter_company_doc
			unlink_inter_company_doc(self.doctype, self.name, self.inter_company_reference)

	def set_title(self):
		if self.letter_of_credit:
			self.title = "{0}/{1}".format(self.letter_of_credit, self.supplier_name)
		else:
			self.title = self.supplier_name or self.supplier

	def update_previous_doc_status(self):
		purchase_orders = set()
		purchase_order_row_names = set()
		purchase_receipt_row_names = set()
		material_requests = set()
		work_orders = set()

		for d in self.items:
			if d.purchase_order:
				purchase_orders.add(d.purchase_order)
			if d.purchase_order_item:
				purchase_order_row_names.add(d.purchase_order_item)
			if d.purchase_receipt_item:
				purchase_receipt_row_names.add(d.purchase_receipt_item)
			if d.material_request:
				material_requests.add(d.material_request)
			if d.work_order:
				work_orders.add(d.work_order)

		# Update Returned Against Purchase Receipt
		if self.is_return and self.return_against:
			doc = frappe.get_doc("Purchase Receipt", self.return_against)
			doc.set_billing_status(update=True)
			doc.validate_returned_qty(from_doctype=self.doctype, row_names=purchase_receipt_row_names)
			doc.validate_billed_qty(from_doctype=self.doctype, row_names=purchase_receipt_row_names)
			doc.notify_update()

		# Update Purchase Orders
		for name in purchase_orders:
			doc = frappe.get_doc("Purchase Order", name)
			doc.set_receipt_status(update=True)
			doc.validate_received_qty(from_doctype=self.doctype, row_names=purchase_order_row_names)
			doc.set_billing_status(update=True)

			doc.set_status(update=True)
			doc.notify_update()

		# Update Material Requests
		for name in material_requests:
			doc = frappe.get_doc("Material Request", name)
			doc.set_completion_status(update=True)
			doc.set_status(update=True)
			doc.notify_update()

		# Update Work Orders
		for name in work_orders:
			doc = frappe.get_doc("Work Order", name)
			doc.run_method("update_status", from_doctype=self.doctype)
			doc.notify_update()

	def update_purchase_order_billing_status(self):
		purchase_orders = set([d.purchase_order for d in self.items if d.purchase_order])
		for name in purchase_orders:
			doc = frappe.get_doc("Purchase Order", name)
			doc.set_billing_status(update=True)
			doc.set_status(update=True)
			doc.notify_update()

	def set_billing_status(self, update=False, update_modified=True):
		data = self.get_billing_status_data()

		# update values in rows
		for d in self.items:
			d.billed_qty = flt(data.billed_qty_map.get(d.name))
			d.billed_amt = flt(data.billed_amount_map.get(d.name))
			d.returned_qty = flt(data.receipt_return_qty_map.get(d.name))
			if update:
				d.db_set({
					'billed_qty': d.billed_qty,
					'billed_amt': d.billed_amt,
					'returned_qty': d.returned_qty,
				}, update_modified=update_modified)

		# update percentage in parent
		self.per_returned = flt(self.calculate_status_percentage('returned_qty', 'qty', self.items))
		self.per_billed = self.calculate_status_percentage('billed_qty', 'qty', self.items)
		self.per_completed = self.calculate_status_percentage(['billed_qty', 'returned_qty'], 'qty', self.items)
		if self.per_completed is None:
			total_billed_qty = flt(sum([flt(d.billed_qty) for d in self.items]), self.precision('total_qty'))
			self.per_billed = 100 if total_billed_qty else 0
			self.per_completed = 100 if total_billed_qty else 0

		# update billing_status
		self.billing_status = self.get_completion_status('per_completed', 'Bill',
			not_applicable=self.status == "Closed" or self.per_returned == 100 or self.is_return,
			not_applicable_based_on='per_billed')

		if update:
			self.db_set({
				'per_billed': self.per_billed,
				'per_returned': self.per_returned,
				'per_completed': self.per_completed,
				'billing_status': self.billing_status,
			}, update_modified=update_modified)

	def get_billing_status_data(self):
		out = frappe._dict()
		out.billed_qty_map = {}
		out.billed_amount_map = {}
		out.receipt_return_qty_map = {}

		if self.docstatus == 1:
			row_names = [d.name for d in self.items]
			if row_names:
				# Billed By Purchase Invoice
				billed_by_pinv = frappe.db.sql("""
					select i.purchase_receipt_item, i.qty, i.amount, p.is_return, p.reopen_order
					from `tabPurchase Invoice Item` i
					inner join `tabPurchase Invoice` p on p.name = i.parent
					where p.docstatus = 1 and (p.is_return = 0 or p.reopen_order = 1)
						and i.purchase_receipt_item in %s
				""", [row_names], as_dict=1)

				for d in billed_by_pinv:
					out.billed_amount_map.setdefault(d.purchase_receipt_item, 0)
					out.billed_amount_map[d.purchase_receipt_item] += d.amount

					out.billed_qty_map.setdefault(d.purchase_receipt_item, 0)
					out.billed_qty_map[d.purchase_receipt_item] += d.qty

				# Returned By Purchase Receipt
				out.receipt_return_qty_map = dict(frappe.db.sql("""
					select i.purchase_receipt_item, -1 * sum(i.qty)
					from `tabPurchase Receipt Item` i
					inner join `tabPurchase Receipt` p on p.name = i.parent
					where p.docstatus = 1 and p.is_return = 1 and i.purchase_receipt_item in %s
					group by i.purchase_receipt_item
				""", [row_names]))

		return out

	def validate_returned_qty(self, from_doctype=None, row_names=None):
		self.validate_completed_qty('returned_qty', 'qty', self.items,
			allowance_type=None, from_doctype=from_doctype, row_names=row_names)

	def validate_billed_qty(self, from_doctype=None, row_names=None):
		self.validate_completed_qty(['billed_qty', 'returned_qty'], 'qty', self.items,
			allowance_type='billing', from_doctype=from_doctype, row_names=row_names)

		if frappe.get_cached_value("Accounts Settings", None, "validate_over_billing_in_purchase_invoice"):
			self.validate_completed_qty('billed_amt', 'amount', self.items,
				allowance_type='billing', from_doctype=from_doctype, row_names=row_names)

	def update_status(self, status):
		self.set_status(status=status)
		self.set_billing_status(update=True)
		self.set_status(update=True, status=status)
		self.update_purchase_order_billing_status()
		self.notify_update()
		clear_doctype_notifications(self)

	def validate_previous_docstatus(self):
		for d in self.get('items'):
			if d.purchase_order and frappe.db.get_value("Purchase Order", d.purchase_order, "docstatus", cache=1) != 1:
				frappe.throw(_("Row #{0}: Purchase Order {1} is not submitted").format(d.idx, d.purchase_order))

		if self.return_against and frappe.db.get_value("Purchase Receipt", self.return_against, "docstatus", cache=1) != 1:
			frappe.throw(_("Return Against Purchase Receipt {0} is not submitted").format(self.return_against))

	def validate_with_previous_doc(self):
		super(PurchaseReceipt, self).validate_with_previous_doc({
			"Purchase Order": {
				"ref_dn_field": "purchase_order",
				"compare_fields": [["supplier", "="], ["company", "="],	["currency", "="],
					["supplier_warehouse", "="]],
			},
			"Purchase Order Item": {
				"ref_dn_field": "purchase_order_item",
				"compare_fields": [["project", "="], ["uom", "="], ["item_code", "="]],
				"is_child_table": True,
				"allow_duplicate_prev_row_id": True
			},
			"Purchase Receipt Item": {
				"ref_dn_field": "purchase_receipt_item",
				"compare_fields": [["project", "="], ["item_code", "="]],
				"is_child_table": True,
				"allow_duplicate_prev_row_id": True
			}
		})

		self.validate_work_orders()

		if cint(frappe.db.get_single_value('Buying Settings', 'maintain_same_rate')) and not self.is_return:
			self.validate_rate_with_reference_doc([["Purchase Order", "purchase_order", "purchase_order_item"]])

	def check_next_docstatus(self):
		submit_rv = frappe.db.sql("""select t1.name
			from `tabPurchase Invoice` t1,`tabPurchase Invoice Item` t2
			where t1.name = t2.parent and t2.purchase_receipt = %s and t1.docstatus = 1""",
			(self.name))
		if submit_rv:
			frappe.throw(_("Purchase Invoice {0} is already submitted").format(self.submit_rv[0][0]))

	def validate_cwip_accounts(self):
		for item in self.get('items'):
			if item.is_fixed_asset and is_cwip_accounting_enabled(item.asset_category):
				# check cwip accounts before making auto assets
				# Improves UX by not giving messages of "Assets Created" before throwing error of not finding arbnb account
				arbnb_account = self.get_company_default("asset_received_but_not_billed")
				cwip_account = get_asset_account("capital_work_in_progress_account", asset_category = item.asset_category, \
					company = self.company)
				break

	def validate_order_required(self):
		if self.is_return:
			return

		po_required = frappe.get_cached_value("Buying Settings", None, 'po_required') == 'Yes'
		if self.get('transaction_type'):
			tt_po_required = frappe.get_cached_value('Transaction Type', self.get('transaction_type'), 'po_required')
			if tt_po_required:
				po_required = tt_po_required == 'Yes'

		if po_required:
			for d in self.get('items'):
				if not d.purchase_order:
					frappe.throw(_("Purchase Order required for Item {0}").format(d.item_code))

	# Check for Closed status
	def check_on_hold_or_closed_status(self):
		check_list =[]
		for d in self.get('items'):
			if (d.meta.get_field('purchase_order') and d.purchase_order
				and d.purchase_order not in check_list):
				check_list.append(d.purchase_order)
				check_on_hold_or_closed_status('Purchase Order', d.purchase_order, is_return=self.get('is_return'))

	def get_gl_entries(self):
		from erpnext.accounts.general_ledger import process_gl_map

		gl_entries = []
		sle_map = self.get_stock_value_difference_map()
		warehouse_account = get_warehouse_account_map(self.company)

		self.make_supplied_items_gl_entry(gl_entries, sle_map, warehouse_account)
		self.make_item_gl_entries(gl_entries, sle_map, warehouse_account)
		self.make_asset_gl_entry(gl_entries)

		return process_gl_map(gl_entries)

	def make_item_gl_entries(self, gl_entries, sle_map, warehouse_account):
		stock_rbnb = self.get_company_default("stock_received_but_not_billed")
		stock_rbnb_currency = get_account_currency(stock_rbnb)
		warehouse_with_no_account = []

		for d in self.get("items"):
			stock_value_diff = flt(sle_map.get((d.name, d.item_code)))
			if not stock_value_diff:
				continue

			if not warehouse_account.get(d.warehouse):
				if d.warehouse not in warehouse_with_no_account:
					warehouse_with_no_account.append(d.warehouse)
				continue

			valuation_net_amount = self.get_item_valuation_net_amount(d)
			valuation_item_tax_amount = self.get_item_valuation_tax_amount(d)

			# debit inventory account
			gl_entries.append(self.get_gl_dict({
				"account": warehouse_account[d.warehouse]["account"],
				"against": stock_rbnb,
				"cost_center": d.cost_center or self.get("cost_center"),
				"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
				"debit": stock_value_diff
			}, warehouse_account[d.warehouse]["account_currency"], item=d))

			# credit stock received but not billed
			if valuation_net_amount or valuation_item_tax_amount:
				gl_entries.append(self.get_gl_dict({
					"account": stock_rbnb,
					"against": warehouse_account[d.warehouse]["account"],
					"cost_center": d.cost_center or self.get("cost_center"),
					"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
					"credit": flt(valuation_net_amount + valuation_item_tax_amount, d.precision('base_net_amount'))
				}, stock_rbnb_currency, item=d))

			# Amount added through landed-cost-voucher
			if flt(d.landed_cost_voucher_amount):
				from erpnext.stock.doctype.landed_cost_voucher.landed_cost_voucher import get_purchase_landed_cost_gl_details
				landed_cost_gl_details = get_purchase_landed_cost_gl_details(self, d)
				for lc_gl_details in landed_cost_gl_details:
					gl_entries.append(self.get_gl_dict({
						"account": lc_gl_details.account_head,
						"against": warehouse_account[d.warehouse]["account"],
						"cost_center": lc_gl_details.cost_center or d.cost_center or self.get("cost_center"),
						"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
						"credit": flt(lc_gl_details.amount),
						"project": d.project
					}, item=d))

			# divisional loss adjustment
			valuation_amount_as_per_doc = valuation_net_amount + valuation_item_tax_amount + \
				flt(d.landed_cost_voucher_amount) + flt(d.get("rm_supp_stock_value_diff"))

			divisional_loss = flt(valuation_amount_as_per_doc - stock_value_diff)

			if divisional_loss:
				loss_account = self.get_company_default("stock_adjustment_account")
				loss_account_currency = get_account_currency(loss_account)

				gl_entries.append(self.get_gl_dict({
					"account": loss_account,
					"against": warehouse_account[d.warehouse]["account"],
					"cost_center": d.cost_center or self.get("cost_center"),
					"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
					"debit": divisional_loss,
					"project": d.project
				}, loss_account_currency, item=d))

		self.validate_warehouse_with_no_account(warehouse_with_no_account)

	def make_asset_gl_entry(self, gl_entries):
		for item in self.get("items"):
			if item.is_fixed_asset:
				if is_cwip_accounting_enabled(item.asset_category):
					self.add_asset_gl_entries(item, gl_entries)
				if flt(item.landed_cost_voucher_amount):
					self.add_lcv_gl_entries(item, gl_entries)
					# update assets gross amount by its valuation rate
					# valuation rate is total of net rate, raw mat supp cost, tax amount, lcv amount per item
					self.update_assets(item, item.valuation_rate)
		return gl_entries

	def add_asset_gl_entries(self, item, gl_entries):
		arbnb_account = self.get_company_default("asset_received_but_not_billed")
		# This returns category's cwip account if not then fallback to company's default cwip account
		cwip_account = get_asset_account("capital_work_in_progress_account", asset_category = item.asset_category, \
			company = self.company)

		asset_amount = flt(item.net_amount) + flt(item.item_tax_amount/self.conversion_rate)
		base_asset_amount = flt(item.base_net_amount + item.item_tax_amount)

		cwip_account_currency = get_account_currency(cwip_account)
		# debit cwip account
		gl_entries.append(self.get_gl_dict({
			"account": cwip_account,
			"against": arbnb_account,
			"cost_center": item.cost_center or self.get("cost_center"),
			"remarks": self.get("remarks") or _("Accounting Entry for Asset"),
			"debit": base_asset_amount,
			"debit_in_account_currency": (base_asset_amount
				if cwip_account_currency == self.company_currency else asset_amount)
		}, item=item))

		asset_rbnb_currency = get_account_currency(arbnb_account)
		# credit arbnb account
		gl_entries.append(self.get_gl_dict({
			"account": arbnb_account,
			"against": cwip_account,
			"cost_center": item.cost_center or self.get("cost_center"),
			"remarks": self.get("remarks") or _("Accounting Entry for Asset"),
			"credit": base_asset_amount,
			"credit_in_account_currency": (base_asset_amount
				if asset_rbnb_currency == self.company_currency else asset_amount)
		}, item=item))

	def add_lcv_gl_entries(self, item, gl_entries):
		expenses_included_in_asset_valuation = self.get_company_default("expenses_included_in_asset_valuation")
		if not is_cwip_accounting_enabled(item.asset_category):
			asset_account = get_asset_category_account(asset_category=item.asset_category, fieldname='fixed_asset_account',
				company=self.company)
		else:
			# This returns company's default cwip account
			asset_account = get_asset_account("capital_work_in_progress_account", company=self.company)

		gl_entries.append(self.get_gl_dict({
			"account": expenses_included_in_asset_valuation,
			"against": asset_account,
			"cost_center": item.cost_center or self.get("cost_center"),
			"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
			"credit": flt(item.landed_cost_voucher_amount),
			"project": item.project
		}, item=item))

		gl_entries.append(self.get_gl_dict({
			"account": asset_account,
			"against": expenses_included_in_asset_valuation,
			"cost_center": item.cost_center or self.get("cost_center"),
			"remarks": self.get("remarks") or _("Accounting Entry for Stock"),
			"debit": flt(item.landed_cost_voucher_amount),
			"project": item.project
		}, item=item))

	def update_assets(self, item, valuation_rate):
		assets = frappe.db.get_all('Asset',
			filters={ 'purchase_receipt': self.name, 'item_code': item.item_code }
		)

		for asset in assets:
			frappe.db.set_value("Asset", asset.name, "gross_purchase_amount", flt(valuation_rate))
			frappe.db.set_value("Asset", asset.name, "purchase_receipt_amount", flt(valuation_rate))

	def set_billed_valuation_amounts(self):
		for d in self.get("items"):
			data = frappe.db.sql("""
				select i.base_net_amount, i.item_tax_amount, i.qty, pi.is_return, pi.update_stock, pi.reopen_order
				from `tabPurchase Invoice Item` i
				inner join `tabPurchase Invoice` pi on pi.name = i.parent
				where pi.docstatus = 1 and i.purchase_receipt_item = %s
			""", d.name, as_dict=1)

			d.billed_net_amount = 0.0
			d.billed_item_tax_amount = 0.0
			d.billed_qty = 0.0

			for pinv_item in data:
				if not pinv_item.is_return or not pinv_item.update_stock:
					d.billed_net_amount += pinv_item.base_net_amount
					d.billed_item_tax_amount += pinv_item.item_tax_amount
				if not pinv_item.is_return or (pinv_item.reopen_order and not pinv_item.update_stock):
					d.billed_qty += pinv_item.qty


@frappe.whitelist()
def make_purchase_invoice(source_name, target_doc=None):
	from frappe.model.mapper import get_mapped_doc

	def get_pending_qty(source_doc):
		return source_doc.qty - source_doc.billed_qty - source_doc.returned_qty

	def item_condition(source, source_parent, target_parent):
		if source.name in [d.purchase_receipt_item for d in target_parent.get('items') if d.purchase_receipt_item]:
			return False

		if source_parent.get('is_return'):
			return get_pending_qty(source) <= 0
		else:
			return get_pending_qty(source) > 0

	def update_item(source_doc, target_doc, source_parent, target_parent):
		target_doc.qty = get_pending_qty(source_doc)
		target_doc.received_qty = target_doc.qty

	def set_missing_values(source, target):
		if len(target.get("items")) == 0:
			frappe.throw(_("All items have already been Invoiced/Returned"))

		doc = frappe.get_doc(target)
		doc.ignore_pricing_rule = 1
		doc.update_stock = 0
		doc.run_method("onload")
		doc.run_method("postprocess_after_mapping")

	mapper = {
		"Purchase Receipt": {
			"doctype": "Purchase Invoice",
			"field_map": {
				"supplier_warehouse":"supplier_warehouse",
				"is_return": "is_return",
				"remarks": "remarks",
			},
			"validation": {
				"docstatus": ["=", 1],
			},
		},
		"Purchase Receipt Item": {
			"doctype": "Purchase Invoice Item",
			"field_map": {
				"name": "purchase_receipt_item",
				"parent": "purchase_receipt",
				"purchase_order_item": "purchase_order_item",
				"purchase_order": "purchase_order",
				"is_fixed_asset": "is_fixed_asset",
				"asset_location": "asset_location",
				"asset_category": "asset_category",
				"batch_no": "batch_no",
				"serial_no": "serial_no",
				"vehicle": "vehicle",
			},
			"postprocess": update_item,
			"condition": item_condition,
		},
		"Purchase Taxes and Charges": {
			"doctype": "Purchase Taxes and Charges",
			"add_if_empty": True
		}
	}

	frappe.utils.call_hook_method("update_purchase_invoice_from_purchase_receipt_mapper", mapper, "Purchase Invoice")

	doclist = get_mapped_doc("Purchase Receipt", source_name, mapper, target_doc, set_missing_values)

	return doclist


@frappe.whitelist()
def make_purchase_return(source_name, target_doc=None):
	from erpnext.controllers.sales_and_purchase_return import make_return_doc
	return make_return_doc("Purchase Receipt", source_name, target_doc)


@frappe.whitelist()
def update_purchase_receipt_status(docname, status):
	pr = frappe.get_doc("Purchase Receipt", docname)
	pr.run_method("update_status", status)


@frappe.whitelist()
def make_stock_entry(source_name,target_doc=None):
	def set_missing_values(source, target):
		target.stock_entry_type = "Material Transfer"
		target.purpose = "Material Transfer"

	doclist = get_mapped_doc("Purchase Receipt", source_name,{
		"Purchase Receipt": {
			"doctype": "Stock Entry",
		},
		"Purchase Receipt Item": {
			"doctype": "Stock Entry Detail",
			"field_map": {
				"warehouse": "s_warehouse",
				"parent": "reference_purchase_receipt"
			},
		},
	}, target_doc, set_missing_values)

	return doclist


@frappe.whitelist()
def make_inter_company_delivery_note(source_name, target_doc=None):
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_inter_company_transaction
	return make_inter_company_transaction("Purchase Receipt", source_name, target_doc)
