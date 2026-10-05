import frappe
from frappe import _
from frappe.model.document import Document


class BinDeterminationRule(Document):
    def validate(self):
        if self.strategy == "Custom":
            from frappe_wms.services.determination import get_putaway_strategies
            if self.custom_strategy not in get_putaway_strategies(): frappe.throw(_("{0} is not a registered putaway strategy (hooks.py wms_putaway_strategies)").format(self.custom_strategy))
