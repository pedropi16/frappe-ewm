import frappe
from frappe import _
from frappe.model.document import Document

class WMSPrintDeterminationRule(Document):
    def validate(self):
        resource_type, warehouse = frappe.db.get_value("WMS Resource", self.output_device, ["resource_type", "warehouse"])
        if resource_type != "Printer":
            frappe.throw(_("Output Device {0} is not a WMS Resource of Resource Type Printer").format(self.output_device))
        if warehouse != self.warehouse:
            frappe.throw(_("Output Device {0} does not belong to warehouse {1}").format(self.output_device, self.warehouse))
