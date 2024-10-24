# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
import erpnext
from frappe import _, scrub
from frappe.core.doctype.user_permission.user_permission import get_permitted_documents
from frappe.model.utils import get_fetch_values
from frappe.utils import getdate, add_years, get_timestamp, nowdate, flt, cstr, cint
from frappe.contacts.doctype.address.address import get_default_address, get_company_address
from frappe.contacts.doctype.contact.contact import get_default_contact
from erpnext.exceptions import PartyFrozen, PartyDisabled, InvalidAccountCurrency
from erpnext.accounts.utils import get_fiscal_year
from erpnext import get_company_currency
from erpnext.overrides.sales_person.sales_person_hooks import get_sales_person_commission_details
from erpnext.accounts.doctype.payment_terms_template.payment_terms_template import get_due_date_from_template


class DuplicatePartyAccountError(frappe.ValidationError):
	pass


@frappe.whitelist()
def get_party_details(
	party=None,
	account=None,
	party_type="Customer",
	letter_of_credit=None,
	bill_to=None,
	company=None,
	posting_date=None,
	delivery_date=None,
	bill_date=None,
	price_list=None,
	currency=None,
	doctype=None,
	ignore_permissions=False,
	payment_terms_template=None,
	transaction_type=None,
	cost_center=None,
	has_stin=None,
	party_address=None,
	shipping_address=None,
	company_address=None,
	contact_person=None,
	pos_profile=None,
	project=None
):
	if not party_type or not party:
		return {}

	if not frappe.db.exists(party_type, party):
		frappe.throw(_("{0}: {1} does not exists").format(party_type, party))

	return _get_party_details(
		party=party,
		account=account,
		party_type=party_type,
		letter_of_credit=letter_of_credit,
		bill_to=bill_to,
		company=company,
		posting_date=posting_date,
		delivery_date=delivery_date,
		bill_date=bill_date,
		price_list=price_list,
		currency=currency,
		doctype=doctype,
		ignore_permissions=ignore_permissions,
		payment_terms_template=payment_terms_template,
		transaction_type=transaction_type,
		cost_center=cost_center,
		has_stin=has_stin,
		party_address=party_address,
		shipping_address=shipping_address,
		company_address=company_address,
		contact_person=contact_person,
		pos_profile=pos_profile,
		project=project,
	)


def _get_party_details(
	party=None,
	account=None,
	party_type="Customer",
	letter_of_credit=None,
	bill_to=None,
	company=None,
	posting_date=None,
	delivery_date=None,
	bill_date=None,
	price_list=None,
	currency=None,
	doctype=None,
	ignore_permissions=False,
	payment_terms_template=None,
	transaction_type=None,
	cost_center=None,
	has_stin=None,
	party_address=None,
	shipping_address=None,
	company_address=None,
	contact_person=None,
	pos_profile=None,
	project=None
):
	if not ignore_permissions and not frappe.has_permission(party_type, "read", party):
		frappe.throw(_("Not permitted for {0}").format(party), frappe.PermissionError)

	party_details = frappe._dict({
		scrub(party_type): party,
	})

	# Determine party
	billing_party_type = party_type
	billing_party = bill_to or party
	if letter_of_credit:
		billing_party_type = "Letter of Credit"
		billing_party = letter_of_credit

	party = frappe.get_cached_doc(party_type, party)
	billing_party_doc = frappe.get_cached_doc(billing_party_type, billing_party) if billing_party_type == party_type else party

	set_basic_values(party_details, party)

	currency = set_currency(party_details, party, company, currency=currency)
	account, cost_center = set_party_account_and_cost_center(party_details, party_type, billing_party_type, billing_party, company,
		transaction_type=transaction_type, account=account, cost_center=cost_center)

	party_address, shipping_address = set_address_details(party_details, party, doctype, company,
		party_address, shipping_address, company_address, bill_to)
	contact_person = set_contact_details(party_details, billing_party_doc, billing_party_type, contact_person, project=project)

	price_list = set_price_list(party_details, party, price_list, pos_profile)

	party_details["tax_category"] = get_address_tax_category(billing_party_doc.get("tax_category"),
		party_address, shipping_address if party_type != "Supplier" else party_address)

	party_details.bill_to_name = billing_party_doc.get('customer_name')
	party_details.tax_id = billing_party_doc.get('tax_id')
	party_details.tax_cnic = billing_party_doc.get('tax_cnic')
	party_details.tax_strn = billing_party_doc.get('tax_strn')
	party_details.tax_status = billing_party_doc.get('tax_status')

	party_details["taxes_and_charges"] = set_taxes(billing_party_doc.name, party_type, posting_date, company,
		customer_group=billing_party_doc.get('customer_group'), supplier_group=billing_party_doc.get('supplier_group'),
		tax_category=billing_party_doc.get('tax_category'),
		tax_id=party_details.tax_id, tax_cnic=party_details.tax_cnic, tax_strn=party_details.tax_strn, has_stin=has_stin,
		transaction_type=transaction_type, cost_center=cost_center,
		billing_address=party_address, shipping_address=shipping_address)

	if not payment_terms_template:
		payment_terms_template = get_payment_terms_template(party_type, billing_party_doc.name, company)
		party_details["payment_terms_template"] = payment_terms_template

	party_details["due_date"] = get_due_date(posting_date, bill_date=bill_date, delivery_date=delivery_date,
		party_type=party_type, party=party, payment_terms_template=payment_terms_template, company=company)

	set_sales_team(party_details, party)

	if doctype == "Sales Order":
		set_credit_balance(party_details, billing_party_doc, doctype, company)

	if doctype == "Sales Invoice":
		set_previous_outstanding_balance(party_details, billing_party_doc, posting_date, account, company, doctype)

	# supplier tax withholding category
	if party_type == "Supplier" and party:
		party_details["supplier_tds"] = party.get("tax_withholding_category")

	return party_details


def set_basic_values(party_details, party):
	to_copy = []
	if party.doctype == "Customer":
		to_copy = ["customer_name", "customer_group", "territory", "language", "default_sales_partner", "default_commission_rate"]
	elif party.doctype == "Supplier":
		to_copy = ["supplier_name", "supplier_group", "language"]
	elif party.doctype == "Lead":
		party_details["customer_name"] = party.get("company_name") or party.get("lead_name")
		to_copy = ["territory"]

	for f in to_copy:
		party_details[f] = party.get(f)


def set_currency(party_details, party, company, currency):
	if not currency:
		currency = party.default_currency if party.get("default_currency") else get_company_currency(company)
	party_details["currency"] = currency

	return currency


def set_party_account_and_cost_center(
	party_details, party_type, billing_party_type, billing_party, company,
	transaction_type, account, cost_center
):
	if billing_party:
		if not account:
			account = get_party_account(billing_party_type, billing_party, company, transaction_type=transaction_type)

		account_fieldname = "debit_to" if party_type == "Customer" else "credit_to"
		party_details[account_fieldname] = account

		cost_center = get_party_cost_center(billing_party_type, billing_party, company, transaction_type=transaction_type)
		if cost_center:
			party_details['cost_center'] = cost_center

	return account, cost_center


def set_address_details(
	party_details, party, doctype, company,
	party_address=None, shipping_address=None, company_address=None, bill_to=None
):
	lead = party.name if party.doctype == "Lead" else None

	# Billing Address
	billing_address_field = "customer_address" if party.doctype == "Lead" else scrub(party.doctype) + "_address"
	party_details[billing_address_field] = party_address or get_default_address(party.doctype, bill_to or party.name)
	party_details.address_display = get_address_display(party_details[billing_address_field], lead=lead)
	if doctype:
		party_details.update(get_fetch_values(doctype, billing_address_field, party_details[billing_address_field]))

	# Company Address
	if company_address:
		party_details.update({'company_address': company_address})
	else:
		party_details.update(get_company_address(company))

	if doctype and frappe.get_meta(doctype).has_field('company_address'):
		party_details.update(get_fetch_values(doctype, 'company_address', party_details.company_address))

	# Shipping Address for Sales
	if party.doctype in ("Customer", "Lead"):
		party_details.shipping_address_name = shipping_address or get_party_shipping_address(party.doctype, party.name)
		party_details.shipping_address = get_address_display(party_details["shipping_address_name"])
		if doctype:
			party_details.update(get_fetch_values(doctype, 'shipping_address_name', party_details.shipping_address_name))

	# Shipping Address for Purchase
	if doctype and doctype in ["Purchase Invoice", "Purchase Order", "Purchase Receipt"]:
		party_details["shipping_address"] = shipping_address or party_details.get("company_address")
		party_details.shipping_address_display = get_address_display(party_details["shipping_address"])
		party_details.update(get_fetch_values(doctype, 'shipping_address', party_details.shipping_address))

	# Regional Address Details
	get_regional_address_details(party_details, doctype, company)

	return party_details.get(billing_address_field), party_details.shipping_address_name


@erpnext.allow_regional
def get_regional_address_details(party_details, doctype, company):
	pass


def set_contact_details(party_details, party, party_type, contact_person=None, project=None):
	if contact_person:
		party_details.contact_person = contact_person

	project_details = None
	if project and party_type == "Customer":
		project_details = frappe.db.get_value("Project", project,
			['customer', 'contact_person', 'contact_mobile', 'contact_phone', 'contact_email'], as_dict=1)

	if not party_details.contact_person and project_details and party.name == project_details.customer:
		party_details.contact_person = project_details.contact_person

	if not party_details.contact_person:
		party_details.contact_person = get_default_contact(party_type, party.name)

	lead = None
	if party_type == "Lead":
		lead = party

	party_details.update(get_contact_details(party_details.contact_person, project=project_details, lead=lead))

	return party_details.contact_person


@frappe.whitelist()
def get_contact_details(contact=None, project=None, lead=None, get_contact_no_list=False, link_doctype=None, link_name=None):
	from crm.crm.utils import get_contact_details

	out = get_contact_details(contact, lead=lead,
		get_contact_no_list=get_contact_no_list, link_doctype=link_doctype, link_name=link_name)
	out = out or frappe._dict()

	if project and contact:
		if isinstance(project, str):
			project = frappe.db.get_value("Project", project, [
				'contact_person', 'contact_mobile', 'contact_phone', 'contact_email'
			], as_dict=1)

		if cstr(contact) == cstr(project.get('contact_person')):
			out.contact_mobile = project.contact_mobile
			out.contact_phone = project.contact_phone
			out.contact_email = project.contact_email

	return out


@frappe.whitelist()
def get_address_display(address=None, lead=None):
	from crm.crm.utils import get_address_display
	return get_address_display(address, lead=lead)


def get_default_price_list(party):
	"""Return default price list for party (Document object)"""
	if party.get("default_price_list"):
		return party.default_price_list

	if party.doctype == "Customer":
		price_list = frappe.get_cached_value("Customer Group", party.customer_group, "default_price_list")
		if price_list:
			return price_list

	return None


def get_retail_price_list(party):
	"""Return default price list for party (Document object)"""
	if party.get("retail_price_list"):
		return party.retail_price_list

	if party.doctype == "Customer":
		price_list = frappe.get_cached_value("Selling Settings", None, "retail_price_list")
		if price_list:
			return price_list

	return None


def set_price_list(party_details, party, given_price_list, pos=None):
	# price list
	price_list = get_permitted_documents('Price List')

	# if there is only one permitted document based on user permissions, set it
	if price_list and len(price_list) == 1:
		price_list = price_list[0]
	elif pos and party.doctype == 'Customer':
		customer_price_list = frappe.get_cached_value("Customer", party.name, "default_price_list")

		if customer_price_list:
			price_list = customer_price_list
		else:
			pos_price_list = frappe.get_cached_value("POS Profile", pos, "selling_price_list")
			price_list = pos_price_list or given_price_list
	else:
		price_list = get_default_price_list(party) or given_price_list

	if price_list:
		party_details.price_list_currency = frappe.get_cached_value("Price List", price_list, "currency")

	price_list_field = "buying_price_list" if party.doctype == "Supplier" else "selling_price_list"
	if not price_list:
		price_list = frappe.db.get_default(price_list_field)

	party_details[price_list_field] = price_list

	party_details["retail_price_list"] = get_retail_price_list(party)

	return price_list


def set_sales_team(party_details, party):
	if party.doctype == "Customer" and party.get("sales_team"):
		party_details["sales_team"] = []
		for d in party.get("sales_team"):
			sales_person_details = frappe._dict({
				"sales_person": d.sales_person,
				"allocated_percentage": d.allocated_percentage or None
			})
			sales_person_details.update(get_sales_person_commission_details(d.sales_person))

			party_details["sales_team"].append(sales_person_details)


def set_credit_balance(party_details, party, doctype, company):
	from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding

	meta = frappe.get_meta(doctype)
	show_credit_limit = meta.has_field('customer_credit_limit')
	show_outstanding_amount = meta.has_field('customer_outstanding_amount')
	show_credit_balance = meta.has_field('customer_credit_balance')

	if show_credit_balance or show_credit_limit:
		party_details["customer_credit_limit"] = get_credit_limit(party.name, company)
	if show_credit_balance or show_outstanding_amount:
		party_details["customer_outstanding_amount"] = get_customer_outstanding(party.name, company)
	if show_credit_balance:
		party_details["customer_credit_balance"] = party_details["customer_credit_limit"] - party_details["customer_outstanding_amount"]


def set_previous_outstanding_balance(party_details, party, posting_date, account, company, doctype):
	meta = frappe.get_meta(doctype)
	if meta.has_field('previous_outstanding_amount') and account and party:
		from erpnext.accounts.utils import get_balance_on
		party_details["previous_outstanding_amount"] = get_balance_on(account, posting_date,
			party_type=party.doctype, party=party.name, company=company,
			ignore_account_permission=1)


@frappe.whitelist()
def get_party_account_details(party_type, party, company, transaction_type=None):
	account = get_party_account(party_type, party, company, transaction_type)
	cost_center = get_party_cost_center(party_type, party, company, transaction_type)

	return frappe._dict({
		'account': account,
		'cost_center': cost_center
	})


@frappe.whitelist()
def get_party_account(party_type, party, company, transaction_type=None):
	"""Returns the account for the given `party`.
		Will first search in party (Customer / Supplier) record, if not found,
		will search in group (Customer Group / Supplier Group),
		finally will return default."""
	if not company:
		frappe.throw(_("Please select a Company"))

	account = None

	if transaction_type:
		transaction_type_doc = frappe.get_cached_doc("Transaction Type", transaction_type)
		account_rows = transaction_type_doc.get('accounts', filters={'company': company})
		party_account_type = erpnext.get_party_account_type(party_type)
		for account_row in account_rows:
			if account_row.account and frappe.get_cached_value("Account", account_row.account, 'account_type') == party_account_type:
				account = account_row.account

	if not account and party:
		party_doc = frappe.get_cached_doc(party_type, party)
		account_row = party_doc.get('accounts', filters={'company': company})
		if account_row:
			account = account_row[0].account

	if not account and party and party_type in ['Customer', 'Supplier']:
		party_group_doctype = "Customer Group" if party_type=="Customer" else "Supplier Group"
		group = frappe.get_cached_value(party_type, party, scrub(party_group_doctype))
		account = frappe.db.get_value("Party Account",
			{"parenttype": party_group_doctype, "parent": group, "company": company}, "account")

	if not account and party_type in ['Customer', 'Supplier', 'Letter of Credit']:
		if party_type == "Customer":
			default_account_name = "default_receivable_account"
		elif party_type == "Supplier":
			default_account_name = "default_payable_account"
		else:
			default_account_name = "default_letter_of_credit_account"

		account = frappe.get_cached_value('Company',  company,  default_account_name)

	if party:
		existing_gle_currency = get_party_gle_currency(party_type, party, company)
		if existing_gle_currency:
			if account:
				account_currency = frappe.get_cached_value("Account", account, "account_currency")
			if (account and account_currency != existing_gle_currency) or not account:
				account = get_party_gle_account(party_type, party, company)

	return account


def get_party_cost_center(party_type, party, company, transaction_type=None):
	from erpnext.accounts.utils import get_allow_cost_center_in_entry_of_bs_account
	if not get_allow_cost_center_in_entry_of_bs_account():
		return

	if not company:
		frappe.throw(_("Please select a Company"))

	cost_center = None

	if transaction_type:
		transaction_type_doc = frappe.get_cached_doc("Transaction Type", transaction_type)
		account_rows = transaction_type_doc.get('accounts', filters={'company': company})
		party_account_type = erpnext.get_party_account_type(party_type)
		for account_row in account_rows:
			if (not account_row.account and account_row.cost_center) or frappe.get_cached_value("Account", account_row.account, 'account_type') == party_account_type:
				cost_center = account_row.cost_center

	if not party:
		return cost_center

	if not cost_center:
		party_doc = frappe.get_cached_doc(party_type, party)
		account_row = party_doc.get('accounts', filters={'company': company})
		if account_row:
			cost_center = account_row[0].cost_center

	return cost_center


@frappe.whitelist()
def get_party_bank_account(party_type, party):
	return frappe.db.get_value('Bank Account', {
		'party_type': party_type,
		'party': party,
		'is_default': 1
	})


def get_party_account_currency(party_type, party, company):
	def generator():
		party_account = get_party_account(party_type, party, company)
		return frappe.db.get_value("Account", party_account, "account_currency", cache=True)

	return frappe.local_cache("party_account_currency", (party_type, party, company), generator)


def get_party_gle_currency(party_type, party, company):
	def generator():
		existing_gle_currency = frappe.db.sql("""select account_currency from `tabGL Entry`
			where docstatus=1 and company=%(company)s and party_type=%(party_type)s and party=%(party)s
			limit 1""", { "company": company, "party_type": party_type, "party": party })

		return existing_gle_currency[0][0] if existing_gle_currency else None

	return frappe.local_cache("party_gle_currency", (party_type, party, company), generator,
		regenerate_if_none=True)


def get_party_gle_account(party_type, party, company):
	def generator():
		existing_gle_account = frappe.db.sql("""select account from `tabGL Entry`
			where docstatus=1 and company=%(company)s and party_type=%(party_type)s and party=%(party)s
			limit 1""", { "company": company, "party_type": party_type, "party": party })

		return existing_gle_account[0][0] if existing_gle_account else None

	return frappe.local_cache("party_gle_account", (party_type, party, company), generator,
		regenerate_if_none=True)


def validate_party_gle_currency(party_type, party, company, party_account_currency=None):
	"""Validate party account currency with existing GL Entry's currency"""
	if not party_account_currency:
		party_account_currency = get_party_account_currency(party_type, party, company)

	existing_gle_currency = get_party_gle_currency(party_type, party, company)

	if existing_gle_currency and party_account_currency != existing_gle_currency:
		frappe.throw(_("Accounting Entry for {0}: {1} can only be made in currency: {2}")
			.format(party_type, party, existing_gle_currency), InvalidAccountCurrency)


def validate_party_accounts(doc):
	companies = []

	for account in doc.get("accounts"):
		if account.company in companies:
			frappe.throw(_("There can only be 1 Account per Company in {0} {1}")
				.format(doc.doctype, doc.name), DuplicatePartyAccountError)
		else:
			companies.append(account.company)

		party_account_currency = frappe.db.get_value("Account", account.account, "account_currency", cache=True)
		existing_gle_currency = get_party_gle_currency(doc.doctype, doc.name, account.company)
		if frappe.db.get_default("Company"):
			company_default_currency = frappe.get_cached_value('Company',
				frappe.db.get_default("Company"),  "default_currency")
		else:
			company_default_currency = frappe.db.get_value('Company', account.company, "default_currency")

		if existing_gle_currency and party_account_currency != existing_gle_currency:
			frappe.throw(_("Accounting entries have already been made in currency {0} for company {1}. Please select a receivable or payable account with currency {0}.").format(existing_gle_currency, account.company))

		if doc.get("default_currency") and party_account_currency and company_default_currency:
			if doc.default_currency != party_account_currency and doc.default_currency != company_default_currency:
				frappe.throw(_("Billing currency must be equal to either default company's currency or party account currency"))


@frappe.whitelist()
def get_due_date(posting_date, bill_date=None, delivery_date=None, payment_terms_template=None, party_type=None, party=None, company=None):
	due_date = bill_date or posting_date

	if not payment_terms_template and party_type and party:
		payment_terms_template = get_payment_terms_template(party_type, party, company)

	if payment_terms_template:
		template_due_date = get_due_date_from_template(payment_terms_template,
			posting_date=posting_date, bill_date=bill_date, delivery_date=delivery_date)
		if template_due_date:
			due_date = template_due_date

	# If due date is calculated from bill_date, check this condition
	if getdate(due_date) < getdate(posting_date):
		due_date = posting_date

	return due_date


@frappe.whitelist()
def get_address_tax_category(tax_category=None, billing_address=None, shipping_address=None):
	addr_tax_category_from = frappe.get_cached_value("Accounts Settings", None, "determine_address_tax_category_from")
	if addr_tax_category_from == "Shipping Address":
		if shipping_address:
			tax_category = frappe.db.get_value("Address", shipping_address, "tax_category") or tax_category
	else:
		if billing_address:
			tax_category = frappe.db.get_value("Address", billing_address, "tax_category") or tax_category

	return cstr(tax_category)


@frappe.whitelist()
def set_taxes(party, party_type, posting_date, company, customer_group=None, supplier_group=None, tax_category=None,
		transaction_type=None, cost_center=None, tax_id=None, tax_cnic=None, tax_strn=None, has_stin=None,
		billing_address=None, shipping_address=None):
	from erpnext.accounts.doctype.tax_rule.tax_rule import get_tax_template, get_party_details

	args = {
		scrub(party_type): party,
		"company": company
	}

	if tax_category:
		args['tax_category'] = tax_category

	if customer_group:
		args['customer_group'] = customer_group

	if supplier_group:
		args['supplier_group'] = supplier_group

	if transaction_type:
		args['transaction_type'] = transaction_type

	if cost_center:
		args['cost_center'] = cost_center

	args['tax_id'] = "Set" if tax_id else "Not Set"
	args['tax_cnic'] = "Set" if tax_cnic else "Not Set"
	args['tax_strn'] = "Set" if tax_strn else "Not Set"

	if has_stin is not None:
		args['has_stin'] = "Yes" if cint(has_stin) else "No"

	if billing_address or shipping_address:
		args.update(get_party_details(party, party_type, {
			"billing_address": billing_address,
			"shipping_address": shipping_address
		}))
	else:
		args.update(get_party_details(party, party_type))

	if party_type in ("Customer", "Lead"):
		args.update({"tax_type": "Sales"})

		if party_type == 'Lead':
			args['customer'] = None
			del args['lead']
	else:
		args.update({"tax_type": "Purchase"})

	return get_tax_template(posting_date, args)


@frappe.whitelist()
def get_payment_terms_template(party_type, party, company=None):
	if party_type not in ("Customer", "Supplier"):
		return

	template = None
	if party_type == 'Customer':
		customer = frappe.get_cached_value("Customer", party, ['payment_terms', "customer_group"], as_dict=1)
		template = customer.payment_terms
		if not template and customer.customer_group:
			template = frappe.get_cached_value("Customer Group", customer.customer_group, 'payment_terms')
	else:
		supplier = frappe.get_cached_value("Supplier", party, ['payment_terms', "supplier_group"], as_dict=1)
		template = supplier.payment_terms
		if not template and supplier.supplier_group:
			template = frappe.get_cached_value("Supplier Group", supplier.supplier_group, 'payment_terms')

	if not template and company:
		template = frappe.get_cached_value('Company',  company,  fieldname='payment_terms')

	return template


def validate_party_frozen_disabled(party_type, party_name):
	if frappe.flags.ignored_closed_or_disabled:
		return

	if party_type and party_name:
		if party_type in ("Customer", "Supplier", "Letter of Credit"):
			party = frappe.get_cached_value(party_type, party_name, ["is_frozen", "disabled"], as_dict=True)
			if party.disabled:
				frappe.throw(_("{0} is disabled").format(frappe.get_desk_link(party_type, party_name)), PartyDisabled)
			elif party.get("is_frozen"):
				frozen_accounts_modifier = frappe.db.get_single_value( 'Accounts Settings', 'frozen_accounts_modifier')
				if frozen_accounts_modifier not in frappe.get_roles():
					frappe.throw(_("{0} is frozen").format(frappe.get_desk_link(party_type, party_name)), PartyFrozen)

		elif party_type == "Employee":
			if frappe.db.get_value("Employee", party_name, "status") == "Left":
				frappe.msgprint(_("{0} is not active").format(frappe.get_desk_link(party_type, party_name)), alert=True)


@frappe.whitelist()
def get_party_name(party_type, party):
	if not party_type or not party:
		return None

	if party_type == "Letter of Credit":
		return party
	else:
		name_fieldname = "title" if party_type in ["Student", "Shareholder"] else scrub(party_type) + "_name"
		return frappe.get_cached_value(party_type, party, name_fieldname)


def set_party_name_in_list(entries):
	party_name_map = get_party_name_map(entries)
	if party_name_map:
		for d in entries:
			if d.get('party_type') and d.get('party'):
				party_name = party_name_map.get((d.get('party_type'), d.get('party')))
				d['party_name'] = party_name or d.get('party')


def get_party_name_map(entries):
	if not entries:
		return {}

	customer_naming_by = frappe.get_cached_value("Selling Settings", None, "cust_master_name")
	supplier_naming_by = frappe.get_cached_value("Buying Settings", None, "supp_master_name")
	employee_naming_by = frappe.get_cached_value("HR Settings", None, "emp_created_by")

	party_name_map = {}

	if customer_naming_by != 'Customer Name':
		customers = list(set([d.get('party') for d in entries if d.get('party_type') == 'Customer' and d.get('party')]))
		if customers:
			customer_names = frappe.db.sql("select name, customer_name from `tabCustomer` where name in %s", [customers])
			for d in customer_names:
				party_name_map[('Customer', d[0])] = d[1]

	if supplier_naming_by != 'Supplier Name':
		suppliers = list(set([d.get('party') for d in entries if d.get('party_type') == 'Supplier' and d.get('party')]))
		if suppliers:
			supplier_names = frappe.db.sql("select name, supplier_name from `tabSupplier` where name in %s", [suppliers])
			for d in supplier_names:
				party_name_map[('Supplier', d[0])] = d[1]

	if employee_naming_by != 'Employee Name':
		employees = list(set([d.get('party') for d in entries if d.get('party_type') == 'Employee' and d.get('party')]))
		if employees:
			employee_names = frappe.db.sql("select name, employee_name from `tabEmployee` where name in %s", [employees])
			for d in employee_names:
				party_name_map[('Employee', d[0])] = d[1]

	return party_name_map


def get_timeline_data(doctype, name):
	'''returns timeline data for the past one year'''
	from frappe.desk.form.load import get_communication_data

	out = {}
	fields = 'creation, count(*)'
	after = add_years(None, -1).strftime('%Y-%m-%d')
	group_by='group by Date(creation)'

	data = get_communication_data(doctype, name, after=after, group_by='group by creation',
		fields='C.creation as creation, count(C.name)',as_dict=False)

	# fetch and append data from Activity Log
	data += frappe.db.sql("""select {fields}
		from `tabActivity Log`
		where (reference_doctype=%(doctype)s and reference_name=%(name)s)
		or (timeline_doctype in (%(doctype)s) and timeline_name=%(name)s)
		or (reference_doctype in ("Quotation", "Opportunity") and timeline_name=%(name)s)
		and status!='Success' and creation > {after}
		{group_by} order by creation desc
		""".format(fields=fields, group_by=group_by, after=after), {
			"doctype": doctype,
			"name": name
		}, as_dict=False)

	timeline_items = dict(data)

	for date, count in timeline_items.items():
		timestamp = get_timestamp(date)
		out.update({ timestamp: count })

	return out


def get_dashboard_info(party_type, party, loyalty_program=None):
	current_fiscal_year = get_fiscal_year(nowdate(), as_dict=True)

	doctype = "Sales Invoice" if party_type=="Customer" else "Purchase Invoice"
	party_field = "bill_to" if party_type=="Customer" else scrub(party_type)

	companies = frappe.get_all(doctype, filters={
		'docstatus': 1,
		party_field: party
	}, distinct=1, fields=['company'])
	companies = companies or [frappe._dict({"company": erpnext.get_default_company()})]
	companies = [d for d in companies if d.company]

	company_wise_info = []

	company_wise_grand_total = frappe.db.sql("""
		select company, sum(debit_in_account_currency) - sum(credit_in_account_currency) as grand_total,
			sum(debit) - sum(credit) as base_grand_total
		from `tabGL Entry`
		where party_type = %s and party=%s and voucher_type = '{0}'
			and (against_voucher = '' or against_voucher is null)
			and posting_date between %s and %s
		group by company
	""".format(doctype), [party_type, party, current_fiscal_year.year_start_date, current_fiscal_year.year_end_date], as_dict=1)

	loyalty_point_details = []

	if party_type == "Customer":
		loyalty_point_details = frappe._dict(frappe.get_all("Loyalty Point Entry",
			filters={
				'customer': party,
				'expiry_date': ('>=', getdate()),
				},
				group_by="company",
				fields=["company", "sum(loyalty_points) as loyalty_points"],
				as_list =1
			))

	company_wise_billing_this_year = frappe._dict()

	for d in company_wise_grand_total:
		company_wise_billing_this_year.setdefault(
			d.company,{
				"grand_total": d.grand_total,
				"base_grand_total": d.base_grand_total
			})

	company_wise_total_unpaid = frappe._dict(frappe.db.sql("""
		select company, sum(debit_in_account_currency) - sum(credit_in_account_currency)
		from `tabGL Entry`
		where party_type = %s and party=%s
		group by company""", (party_type, party)))

	for d in companies:
		company_default_currency = frappe.get_cached_value("Company", d.company, 'default_currency')
		party_account_currency = get_party_account_currency(party_type, party, d.company)

		if party_account_currency==company_default_currency:
			billing_this_year = flt(company_wise_billing_this_year.get(d.company,{}).get("base_grand_total"))
		else:
			billing_this_year = flt(company_wise_billing_this_year.get(d.company,{}).get("grand_total"))

		total_unpaid = flt(company_wise_total_unpaid.get(d.company))

		if loyalty_point_details:
			loyalty_points = loyalty_point_details.get(d.company)

		info = {}
		info["currency"] = party_account_currency
		info["company"] = d.company

		if party_type == "Customer" and loyalty_point_details:
			info["loyalty_points"] = loyalty_points

		has_permission = False
		if party_type == "Customer" and (frappe.has_permission("Sales Order") or frappe.has_permission("Sales Invoice")):
			has_permission = True
		if party_type == "Supplier" and (frappe.has_permission("Purchase Order") or frappe.has_permission("Purchase Invoice")):
			has_permission = True

		if has_permission:
			info["billing_this_year"] = flt(billing_this_year) if billing_this_year else 0
			info["total_unpaid"] = flt(total_unpaid) if total_unpaid else 0
			if party_type == "Supplier":
				info["billing_this_year"] = -1 * info["billing_this_year"]
				info["total_unpaid"] = -1 * info["total_unpaid"]

		company_wise_info.append(info)

	return company_wise_info


def get_party_shipping_address(doctype, name):
	"""
	Returns an Address name (best guess) for the given doctype and name for which `address_type == 'Shipping'` is true.
	and/or `is_shipping_address = 1`.

	It returns an empty string if there is no matching record.

	:param doctype: Party Doctype
	:param name: Party name
	:return: String
	"""
	out = frappe.db.sql(
		'SELECT dl.parent '
		'from `tabDynamic Link` dl join `tabAddress` ta on dl.parent=ta.name '
		'where '
		'dl.link_doctype=%s '
		'and dl.link_name=%s '
		'and dl.parenttype="Address" '
		'and ifnull(ta.disabled, 0) = 0 and'
		'(ta.address_type="Shipping" or ta.is_shipping_address=1) '
		'order by ta.is_shipping_address desc, ta.address_type desc limit 1',
		(doctype, name)
	)
	if out:
		return out[0][0]
	else:
		return ''


def get_partywise_advanced_payment_amount(party_type, posting_date=None):
	dr_or_cr = "credit - debit" if party_type == "Customer" else "debit - credit"
	advance_condition = "{0} > 0".format(dr_or_cr)
	date_condition = "and posting_date <= %(posting_date)s" if posting_date else ""

	data = frappe.db.sql("""
		SELECT party, sum({dr_or_cr}) as amount
		FROM `tabGL Entry`
		WHERE party_type = %(party_type)s
			and (against_voucher = '' or against_voucher is null)
			and {advance_condition} {date_condition}
		GROUP BY party
	""".format(dr_or_cr=dr_or_cr, advance_condition=advance_condition, date_condition=date_condition),  # nosec
		{"party_type": party_type, "posting_date": posting_date})

	return frappe._dict(data or {})
