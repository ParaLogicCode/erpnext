import frappe


def setup(company=None, patch=True):
	add_custom_roles_for_reports()


def add_custom_roles_for_reports():
	reports = [
		"FBR Sales Tax Report",
		"FBR Advance Tax Report",
		"SRB Service Tax Report",
	]

	for report in reports:
		if not frappe.db.get_value("Custom Role", dict(report=report)):
			frappe.get_doc(
				dict(
					doctype="Custom Role",
					report=report,
					roles=[dict(role="Accounts User"), dict(role="Accounts Manager"), dict(role="Auditor")],
				)
			).insert()
