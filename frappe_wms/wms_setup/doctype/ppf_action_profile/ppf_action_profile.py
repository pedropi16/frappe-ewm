import frappe
from frappe.model.document import Document


class PPFActionProfile(Document):
    def on_update(self):
        frappe.cache.delete_value("wms_ppf_doctypes")

    def on_trash(self):
        frappe.cache.delete_value("wms_ppf_doctypes")
