import frappe
from frappe import _
from frappe.model.document import Document

class RemovalRule(Document):
    def validate(self):
        if self.strategy == "Stringent FIFO" and self.sort_fields:
            frappe.throw(_("Stringent FIFO enforces strict warehouse-wide receipt order and cannot combine with custom Sort Fields"))
        if self.strategy == "Custom":
            from frappe_wms.services.removal_rules import get_removal_strategies
            if self.custom_strategy not in get_removal_strategies(): frappe.throw(_("{0} is not a registered removal strategy (hooks.py wms_removal_strategies)").format(self.custom_strategy))
        if self.strategy == "Fixed Bin" and not self.fixed_bin:
            frappe.throw(_("Strategy 'Fixed Bin' requires Fixed Bin to be set"))
