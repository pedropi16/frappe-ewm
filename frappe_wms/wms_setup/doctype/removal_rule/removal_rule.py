import frappe
from frappe import _
from frappe.model.document import Document

class RemovalRule(Document):
    def validate(self):
        if self.strategy == "Stringent FIFO" and self.sort_fields:
            frappe.throw(_("Stringent FIFO enforces strict warehouse-wide receipt order and cannot combine with custom Sort Fields"))
        if self.strategy == "Fixed Bin" and not self.fixed_bin:
            frappe.throw(_("Strategy 'Fixed Bin' requires Fixed Bin to be set"))
