import frappe
from crm.crm.doctype.customer_feedback.customer_feedback import CustomerFeedback
from erpnext.stock.get_item_details import get_applies_to_details, get_force_applies_to_fields


class CustomerFeedbackERP(CustomerFeedback):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.force_applies_to_fields = get_force_applies_to_fields(self.doctype)

	def set_missing_values(self):
		super().set_missing_values()
		self.set_applies_to_details()

	def set_applies_to_details(self):
		args = self.as_dict()
		if self.reference_doctype == "Project" and self.reference_name:
			args.project = self.reference_name

		applies_to_details = get_applies_to_details(args, for_validate=True)

		for k, v in applies_to_details.items():
			if self.meta.has_field(k) and not self.get(k) or k in self.force_applies_to_fields:
				self.set(k, v)

	@classmethod
	def get_allowed_party_types(cls):
		return super().get_allowed_party_types() + ["Customer"]

	def make_communication_doc(self, for_field, set_timeline_links):
		communication_doc = super().make_communication_doc(for_field, set_timeline_links)

		if set_timeline_links:
			if self.get("applies_to_serial_no"):
				communication_doc.append("timeline_links", {
					"link_doctype": "Serial No",
					"link_name": self.applies_to_serial_no,
				})

			if 'Vehicles' in frappe.get_active_domains() and self.applies_to_vehicle:
				communication_doc.append("timeline_links", {
					"link_doctype": "Vehicle",
					"link_name": self.applies_to_vehicle,
				})

		return communication_doc
