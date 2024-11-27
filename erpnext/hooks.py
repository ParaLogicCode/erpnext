from frappe import _

app_name = "erpnext"
app_title = "ERPNext"
app_publisher = "Frappe Technologies Pvt. Ltd."
app_description = """ERP made simple"""
app_icon = "fa fa-th"
app_color = "#e74c3c"
app_email = "info@erpnext.com"
app_license = "GNU General Public License (v3)"
source_link = "https://github.com/frappe/erpnext"
app_logo_url = '/assets/erpnext/images/erp-icon.svg'
required_apps = ["ParaLogicTech/crm"]


develop_version = '14.x.x-develop'

app_include_js = "erpnext.bundle.js"
app_include_css = "erpnext.bundle.css"

email_css = "email_erpnext.bundle.css"

doctype_js = {
	"Communication": "overrides/communication_hooks.js",
	"Event": "overrides/event_hooks.js",

	"Sales Person": "overrides/sales_person/sales_person_hooks.js",
	"Territory": "overrides/territory/territory_hooks.js",
	"Lead": "overrides/lead/lead_hooks.js",
	"Opportunity": "overrides/opportunity/opportunity_hooks.js",
	"Appointment Type": "overrides/appointment_type/appointment_type_hooks.js",
	"Appointment": "overrides/appointment/appointment_hooks.js",
	"Customer Feedback": "overrides/customer_feedback/customer_feedback_hooks.js",
}

doctype_list_js = {
	"Appointment": "overrides/appointment/appointment_list_hooks.js",
	"Opportunity": "overrides/opportunity/opportunity_list_hooks.js",
	"Customer Feedback": "overrides/customer_feedback/customer_feedback_list_hooks.js",
}

doctype_tree_js = {
	"Sales Person": "overrides/sales_person/sales_person_tree_hooks.js",
}

override_doctype_class = {
	"Sales Person": "erpnext.overrides.sales_person.sales_person_hooks.SalesPersonERP",
	"Territory": "erpnext.overrides.territory.territory_hooks.TerritoryERP",
	"Lead": "erpnext.overrides.lead.lead_hooks.LeadERP",
	"Opportunity": "erpnext.overrides.opportunity.opportunity_hooks.OpportunityERP",
	"Appointment Type": "erpnext.overrides.appointment_type.appointment_type_hooks.AppointmentTypeERP",
	"Appointment": "erpnext.overrides.appointment.appointment_hooks.AppointmentERP",
	"Customer Feedback": "erpnext.overrides.customer_feedback.customer_feedback_hooks.CustomerFeedbackERP",
}

override_doctype_dashboards = {
	"Sales Person": "erpnext.overrides.sales_person.sales_person_hooks.override_sales_person_dashboard",
	"Lead": "erpnext.overrides.lead.lead_hooks.override_lead_dashboard",
	"Opportunity": "erpnext.overrides.opportunity.opportunity_hooks.override_opportunity_dashboard",
	"Appointment": "erpnext.overrides.appointment.appointment_hooks.override_appointment_dashboard",
}

fixtures = [
	{
		"doctype": "Custom Field",
		"filters": {
			"name": ["in", [
				"Sales Person-employee",
				"Sales Person-employee_name",
				"Sales Person-cb_employee",
				"Sales Person-branch",
				"Sales Person-department",
				"Sales Person-designation",
				"Sales Person-sales_commission_category",
				"Sales Person-targets_section",
				"Sales Person-targets",

				"Territory-targets_section",
				"Territory-targets",

				"Campaign-claim_customer",
				"Campaign-claim_customer_name",

				"Lead-customer",

				"Opportunity-company",
				"Opportunity-maintenance_schedule",
				"Opportunity-maintenance_schedule_row",
				"Opportunity-applies_to_variant_of",
				"Opportunity-applies_to_variant_of_name",
				"Opportunity-applies_to_serial_no",
				"Opportunity-applies_to_item",
				"Opportunity-applies_to_item_name",

				"Opportunity Item-item_code",
				"Opportunity Item-uom",
				"Opportunity Item-item_group",
				"Opportunity Item-brand",

				"Appointment Type-company",
				"Appointment Type-holiday_list",

				"Appointment-company",
				"Appointment-applies_to_variant_of",
				"Appointment-applies_to_variant_of_name",
				"Appointment-applies_to_serial_no",
				"Appointment-applies_to_item",
				"Appointment-applies_to_item_name",

				"Appointment-sec_project_template",
				"Appointment-project_template",
				"Appointment-cb1_project_template",
				"Appointment-project_template_name",

				"Customer Feedback-applies_to_variant_of",
				"Customer Feedback-applies_to_variant_of_name",
				"Customer Feedback-applies_to_serial_no",
				"Customer Feedback-applies_to_item",
				"Customer Feedback-applies_to_item_name",
			]]
		}
	},
]

welcome_email = "erpnext.setup.utils.welcome_email"

# setup wizard
setup_wizard_requires = "assets/erpnext/js/setup_wizard.js"
setup_wizard_stages = "erpnext.setup.setup_wizard.setup_wizard.get_setup_stages"
setup_wizard_test = "erpnext.setup.setup_wizard.test_setup_wizard.run_setup_wizard_test"

before_install = "erpnext.setup.install.check_setup_wizard_not_completed"
after_install = "erpnext.setup.install.after_install"

boot_session = "erpnext.startup.boot.boot_session"
notification_config = "erpnext.startup.notifications.get_notification_config"
get_help_messages = "erpnext.utilities.activation.get_help_messages"
get_user_progress_slides = "erpnext.utilities.user_progress.get_user_progress_slides"
update_and_get_user_progress = "erpnext.utilities.user_progress_utils.update_default_domain_actions_and_get_state"
leaderboards = "erpnext.startup.leaderboard.get_leaderboards"

treeviews = ['Account', 'Cost Center', 'Warehouse', 'Item Group', 'Customer Group', 'Assessment Group', 'Department']

email_append_to = ["Job Applicant", "Issue"]

calendars = ["Task", "Work Order", "Leave Application", "Sales Order", "Holiday List", "Course Schedule"]

domains = {
	'Agriculture': 'erpnext.domains.agriculture',
	'Distribution': 'erpnext.domains.distribution',
	'Education': 'erpnext.domains.education',
	'Healthcare': 'erpnext.domains.healthcare',
	'Hospitality': 'erpnext.domains.hospitality',
	'Manufacturing': 'erpnext.domains.manufacturing',
	'Non Profit': 'erpnext.domains.non_profit',
	'Retail': 'erpnext.domains.retail',
	'Services': 'erpnext.domains.services',
}

website_context = {
	"favicon": 	"/assets/erpnext/images/favicon.png",
	"splash_image": "/assets/erpnext/images/erp-icon.svg"
}

dump_report_map = "erpnext.startup.report_data_map.data_map"

before_tests = "erpnext.setup.utils.before_tests"

standard_queries = {
	"Customer": "erpnext.controllers.queries.customer_query",
}

doc_events = {
	"User": {
		"after_insert": "frappe.contacts.doctype.contact.contact.update_contact",
		"validate": "erpnext.hr.doctype.employee.employee.validate_employee_role",
		"on_update": ["erpnext.hr.doctype.employee.employee.update_user_permissions"]
	},
	"Sales Invoice": {
		"on_submit": ["erpnext.regional.create_transaction_log", "erpnext.regional.italy.utils.sales_invoice_on_submit"],
		"on_cancel": "erpnext.regional.italy.utils.sales_invoice_on_cancel",
		"on_trash": "erpnext.regional.check_deletion_permission"
	},
	"Purchase Invoice": {
		"validate": "erpnext.regional.india.utils.update_grand_total_for_rcm"
	},
	"Payment Entry": {
		"on_submit": ["erpnext.regional.create_transaction_log", "erpnext.accounts.doctype.payment_request.payment_request.update_payment_req_status"],
		"on_trash": "erpnext.regional.check_deletion_permission"
	},
	'Address': {
		'validate': ['erpnext.regional.india.utils.validate_gstin_for_india', 'erpnext.regional.italy.utils.set_state_code', 'erpnext.regional.india.utils.update_gst_category']
	},
	('Sales Invoice', 'Sales Order', 'Delivery Note', 'Purchase Invoice', 'Purchase Order', 'Purchase Receipt'): {
		'validate': ['erpnext.regional.india.utils.set_place_of_supply']
	},
	"Contact": {
		"on_trash": "erpnext.support.doctype.issue.issue.update_issue",
	},
}

naming_series_variables = {
	"FY": "erpnext.accounts.utils.parse_naming_series_variable",
	"CO": "erpnext.accounts.utils.parse_naming_series_variable",
}

scheduler_events = {
	"all": [
		"erpnext.healthcare.doctype.patient_appointment.patient_appointment.set_appointment_reminder",
		"erpnext.maintenance.doctype.maintenance_schedule.maintenance_schedule.send_maintenance_schedule_reminder_notifications",
		"erpnext.selling.doctype.customer.customer.send_customer_birthday_notifications",
	],
	"hourly": [
		'erpnext.hr.doctype.daily_work_summary_group.daily_work_summary_group.trigger_emails',
		"erpnext.accounts.doctype.subscription.subscription.process_all",
		"erpnext.erpnext_integrations.doctype.amazon_mws_settings.amazon_mws_settings.schedule_get_order_details",
		"erpnext.erpnext_integrations.doctype.plaid_settings.plaid_settings.automatic_synchronization",
		"erpnext.hr.doctype.shift_type.shift_type.process_auto_attendance_for_all_shifts",
		"erpnext.support.doctype.issue.issue.set_service_level_agreement_variance",
		"erpnext.erpnext_integrations.fbr_pos_integration.post_fbr_pos_invoices_without_number",
	],
	"daily": [
		"erpnext.stock.reorder_item.reorder_item",
		"erpnext.support.doctype.issue.issue.auto_close_tickets",
		"erpnext.controllers.transaction_controller.set_invoice_as_overdue",
		"erpnext.accounts.doctype.fiscal_year.fiscal_year.auto_create_fiscal_year",
		"erpnext.hr.doctype.employee.employee.send_employee_birthday_notification",
		"erpnext.hr.doctype.employee.employee.send_employee_anniversary_notification",
		"erpnext.projects.doctype.task.task.set_tasks_as_overdue",
		"erpnext.assets.doctype.asset.depreciation.post_depreciation_entries",
		"erpnext.hr.doctype.daily_work_summary_group.daily_work_summary_group.send_summary",
		"erpnext.stock.doctype.serial_no.serial_no.update_maintenance_status",
		"erpnext.buying.doctype.supplier_scorecard.supplier_scorecard.refresh_scorecards",
		"erpnext.setup.doctype.company.company.cache_companies_monthly_sales_history",
		"erpnext.assets.doctype.asset.asset.update_maintenance_status",
		"erpnext.assets.doctype.asset.asset.make_post_gl_entry",
		"erpnext.quality_management.doctype.quality_review.quality_review.review",
		"erpnext.support.doctype.service_level_agreement.service_level_agreement.check_agreement_status",
		"erpnext.selling.doctype.quotation.quotation.set_expired_status",
		"erpnext.maintenance.doctype.maintenance_schedule.maintenance_schedule.create_opportunity_from_schedule",
	],
	"daily_long": [
		"erpnext.setup.doctype.email_digest.email_digest.send",
		"erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool.update_latest_price_in_all_boms",
		"erpnext.hr.doctype.leave_ledger_entry.leave_ledger_entry.process_expired_allocation",
		"erpnext.hr.doctype.leave_encashment.leave_encashment.generate_leave_encashment",
		"erpnext.maintenance.doctype.maintenance_schedule.maintenance_schedule.auto_schedule_next_project_templates",
	],
	"monthly_long": [
		"erpnext.accounts.deferred_revenue.convert_deferred_revenue_to_income",
		"erpnext.accounts.deferred_revenue.convert_deferred_expense_to_expense",
		"erpnext.hr.utils.allocate_earned_leaves"
	]
}

get_translated_dict = {
	("doctype", "Global Defaults"): "frappe.geo.country_info.get_translated_dict"
}

get_site_info = 'erpnext.utilities.get_site_info'

payment_gateway_enabled = "erpnext.accounts.utils.create_payment_gateway_account"

jinja = {
	'methods': [
		'erpnext.stock.utils.format_item_name'
	]
}

regional_overrides = {
	'France': {
		'erpnext.tests.test_regional.test_method': 'erpnext.regional.france.utils.test_method'
	},
	'India': {
		'erpnext.tests.test_regional.test_method': 'erpnext.regional.india.utils.test_method',
		'erpnext.controllers.taxes_and_totals.get_itemised_tax_breakup_header': 'erpnext.regional.india.utils.get_itemised_tax_breakup_header',
		'erpnext.controllers.taxes_and_totals.get_itemised_tax_breakup_data': 'erpnext.regional.india.utils.get_itemised_tax_breakup_data',
		'erpnext.accounts.party.get_regional_address_details': 'erpnext.regional.india.utils.get_regional_address_details',
		'erpnext.hr.utils.calculate_annual_eligible_hra_exemption': 'erpnext.regional.india.utils.calculate_annual_eligible_hra_exemption',
		'erpnext.hr.utils.calculate_hra_exemption_for_period': 'erpnext.regional.india.utils.calculate_hra_exemption_for_period',
		'erpnext.accounts.doctype.purchase_invoice.purchase_invoice.make_regional_gl_entries': 'erpnext.regional.india.utils.make_regional_gl_entries'
	},
	'United Arab Emirates': {
		'erpnext.controllers.taxes_and_totals.update_itemised_tax_data': 'erpnext.regional.united_arab_emirates.utils.update_itemised_tax_data'
	},
	'Saudi Arabia': {
		'erpnext.controllers.taxes_and_totals.update_itemised_tax_data': 'erpnext.regional.united_arab_emirates.utils.update_itemised_tax_data'
	},
	'Italy': {
		'erpnext.controllers.taxes_and_totals.update_itemised_tax_data': 'erpnext.regional.italy.utils.update_itemised_tax_data',
		'erpnext.controllers.accounts_controller.validate_regional': 'erpnext.regional.italy.utils.sales_invoice_validate',
	}
}

# ERPNext doctypes for Global Search
global_search_doctypes = {
	"Default": [
		{"doctype": "Customer", "index": 0},
		{"doctype": "Supplier", "index": 1},
		{"doctype": "Item", "index": 2},
		{"doctype": "Warehouse", "index": 3},
		{"doctype": "Account", "index": 4},
		{"doctype": "Employee", "index": 5},
		{"doctype": "BOM", "index": 6},
		{"doctype": "Sales Invoice", "index": 7},
		{"doctype": "Sales Order", "index": 8},
		{"doctype": "Quotation", "index": 9},
		{"doctype": "Work Order", "index": 10},
		{"doctype": "Purchase Receipt", "index": 11},
		{"doctype": "Purchase Invoice", "index": 12},
		{"doctype": "Delivery Note", "index": 13},
		{"doctype": "Stock Entry", "index": 14},
		{"doctype": "Material Request", "index": 15},
		{"doctype": "Delivery Trip", "index": 16},
		{"doctype": "Pick List", "index": 17},
		{"doctype": "Salary Slip", "index": 18},
		{"doctype": "Leave Application", "index": 19},
		{"doctype": "Expense Claim", "index": 20},
		{"doctype": "Payment Entry", "index": 21},
		{"doctype": "Purchase Taxes and Charges Template", "index": 22},
		{"doctype": "Sales Taxes and Charges", "index": 23},
		{"doctype": "Asset", "index": 24},
		{"doctype": "Project", "index": 25},
		{"doctype": "Task", "index": 26},
		{"doctype": "Timesheet", "index": 27},
		{"doctype": "Issue", "index": 28},
		{"doctype": "Serial No", "index": 29},
		{"doctype": "Batch", "index": 30},
		{"doctype": "Branch", "index": 31},
		{"doctype": "Department", "index": 32},
		{"doctype": "Employee Grade", "index": 33},
		{"doctype": "Designation", "index": 34},
		{"doctype": "Job Opening", "index": 35},
		{"doctype": "Job Applicant", "index": 36},
		{"doctype": "Job Offer", "index": 37},
		{"doctype": "Salary Structure Assignment", "index": 38},
		{"doctype": "Appraisal", "index": 39},
		{"doctype": "Loan", "index": 40},
		{"doctype": "Maintenance Schedule", "index": 41},
		{"doctype": "Maintenance Visit", "index": 42},
		{"doctype": "Warranty Claim", "index": 43},
	]
}
