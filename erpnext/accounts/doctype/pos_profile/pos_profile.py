# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import msgprint, _
from frappe.model.document import Document


class POSProfile(Document):
	def validate(self):
		self.validate_default_profile()
		set_account_for_mode_of_payment(self)
		self.validate_all_link_fields()
		self.check_default_payment()

	def validate_default_profile(self):
		for row in self.applicable_for_users:
			res = frappe.db.sql("""select pf.name
				from
					`tabPOS Profile User` pfu, `tabPOS Profile` pf
				where
					pf.name = pfu.parent and pfu.user = %s and pf.name != %s and pf.company = %s
					and pfu.default=1 and pf.disabled = 0""", (row.user, self.name, self.company))

			if row.default and res:
				msgprint(_("Already set default in pos profile {0} for user {1}, kindly disabled default")
					.format(res[0][0], row.user), raise_exception=1)
			elif not row.default and not res:
				msgprint(_("User {0} doesn't have any default POS Profile. Check Default at Row {1} for this User.")
					.format(row.user, row.idx))

	def validate_all_link_fields(self):
		accounts = {
			"Account": [self.income_account, self.expense_account],
			"Cost Center": [self.cost_center],
			"Warehouse": [self.warehouse]
		}

		for link_dt, dn_list in accounts.items():
			for link_dn in dn_list:
				if link_dn and not frappe.db.exists({"doctype": link_dt,
						"company": self.company, "name": link_dn}):
					frappe.throw(_("{0} does not belong to Company {1}").format(link_dn, self.company))

	def check_default_payment(self):
		if self.payments:
			default_mode_of_payment = [d.default for d in self.payments if d.default]
			if len(default_mode_of_payment) > 1:
				frappe.throw(_("Multiple default mode of payment is not allowed"))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def pos_profile_query(doctype, txt, searchfield, start, page_len, filters):
	filters = filters or {}

	user = filters.get('user') or frappe.session['user']
	company = filters.get('company') or frappe.defaults.get_user_default('company')
	branch = filters.get('branch') or frappe.defaults.get_user_default('branch')

	args = {
		'user': user,
		'start': start,
		'company': company,
		'branch': branch,
		'page_len': page_len,
		'txt': '%%%s%%' % txt
	}

	pos_profile = []

	if not pos_profile:
		branch_condition = ""
		if branch:
			branch_condition = " and branch = %(branch)s"

		pos_profile = frappe.db.sql(f"""
			select pf.name
			from `tabPOS Profile` pf
			inner join `tabPOS Profile User` pfu on pfu.parent = pf.name
			where pfu.user = %(user)s
				and pf.company = %(company)s
				and (pf.name like %(txt)s)
				and pf.disabled = 0
				{branch_condition}
			limit %(start)s, %(page_len)s
		""", args)

	if not pos_profile and branch and company:
		pos_profile = frappe.db.sql("""
			select pf.name
			from `tabPOS Profile` pf
			where
				pf.company = %(company)s
				and pf.branch = %(branch)s
				and pf.name like %(txt)s
				and pf.disabled = 0
		""", args)

	if not pos_profile and company:
		pos_profile = frappe.db.sql("""
			select pf.name
			from `tabPOS Profile` pf
			where
				pf.company = %(company)s
				and ifnull(pf.branch, '') = ''
				and pf.name like %(txt)s
				and pf.disabled = 0
		""", args)

	return pos_profile


def set_account_for_mode_of_payment(self, force=False):
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
	pos_profile = self.get("pos_profile") if self.doctype != "POS Profile" else None
	for data in self.payments:
		if not data.account or force:
			data.account = get_bank_cash_account(data.mode_of_payment, self.company, pos_profile=pos_profile).get("account")


@frappe.whitelist()
def get_pos_profile(company, branch=None, user=None):
	if not user:
		user = frappe.session.user

	pos_profile = None

	# User Specific
	if not pos_profile:
		branch_condition = ""
		if branch:
			branch_condition = " AND branch = %(branch)s"

		pos_profile = frappe.db.sql_list(f"""
			SELECT pf.name
			FROM `tabPOS Profile` pf
			INNER JOIN `tabPOS Profile User` pfu ON pf.name = pfu.parent
			WHERE pfu.user = %(user)s
				AND pfu.`default` = 1
				AND pf.company = %(company)s
				AND pf.disabled = 0
				{branch_condition}
		""", {
			'user': user, 'company': company, 'branch': branch,
		})
		pos_profile = pos_profile[0] if pos_profile else None

	# Branch Default
	if not pos_profile and branch and company:
		pos_profile = frappe.db.sql_list("""
			SELECT pf.name
			FROM `tabPOS Profile` pf
			WHERE pf.company = %(company)s AND pf.branch = %(branch)s AND pf.disabled = 0
		""", {
			'company': company, 'branch': branch,
		})
		pos_profile = pos_profile[0] if pos_profile else None

	# Company Default
	if not pos_profile and company:
		pos_profile = frappe.db.sql_list("""
			SELECT pf.name
			FROM `tabPOS Profile` pf
			WHERE pf.company = %(company)s AND ifnull(pf.branch, '') = '' AND pf.disabled = 0
		""", {
			'company': company
		})
		pos_profile = pos_profile[0] if pos_profile else None

	return pos_profile


@frappe.whitelist()
def cashiers_query(doctype, txt, searchfield, start, page_len, filters):
	from frappe.core.doctype.user.user import user_query

	filters = filters or {}
	pos_profile = filters.pop("pos_profile", None)
	cashiers = get_cashiers(pos_profile)

	if not cashiers:
		return []

	filters['name'] = ['in', cashiers]

	return user_query(doctype, txt, searchfield, start, page_len, filters)


def get_cashiers(pos_profile=None):
	cashier_filters = {}
	if pos_profile:
		cashier_filters["parent"] = pos_profile

	cashiers = frappe.get_all(
		"POS Profile User",
		filters=cashier_filters,
		fields='distinct user as user',
		pluck='user'
	)

	return cashiers


def check_is_pos_open(user, pos_profile, throw=False):
	from erpnext.accounts.doctype.pos_opening_entry.pos_opening_entry import get_pos_opening_entry

	if not pos_profile or not user:
		return

	pos_opening_entry_mandatory = frappe.get_cached_value("POS Profile", pos_profile, "pos_opening_entry_mandatory")
	if not pos_opening_entry_mandatory:
		return

	if not get_pos_opening_entry(user, pos_profile):
		message = _("POS Opening Entry is mandatory for POS Transaction.")
		message += " " + "<a href='/app/pos-opening-entry/new-pos-opening-entry' target='_blank'>{0}</a>".format(
			_("Please create POS Opening Entry")
		)
		frappe.msgprint(message, raise_exception=throw)


@frappe.whitelist()
def get_cash_denominations():
	settings = frappe.get_cached_doc("POS Settings", None)
	out = [{"denomination": d.denomination} for d in settings.cash_denominations if d.denomination]
	return out
