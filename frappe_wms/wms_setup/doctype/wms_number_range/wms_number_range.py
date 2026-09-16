import frappe
from frappe import _
from frappe.model.document import Document


class WMSNumberRange(Document):
    def validate(self):
        if self.end_number <= self.start_number:
            frappe.throw(_("End Number must be greater than Start Number"))
        if self.current_number and self.current_number > self.end_number:
            frappe.throw(_("Current Number cannot exceed End Number"))
        if self.range_for != "Handling Unit" and self.hu_type:
            frappe.throw(_("Handling Unit Type only applies to a range for Handling Unit"))
        duplicate = frappe.db.exists("WMS Number Range", {
            "name": ["!=", self.name],
            "range_for": self.range_for,
            "warehouse": self.warehouse or "",
            "hu_type": self.hu_type or "",
            "active": 1,
        })
        if duplicate and self.active:
            frappe.throw(_("Another active Number Range already covers this same warehouse/HU type combination"))
