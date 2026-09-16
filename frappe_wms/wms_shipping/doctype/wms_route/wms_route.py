import frappe
from frappe import _
from frappe.model.document import Document
from frappe_wms.utils import require_storage_role

class WMSRoute(Document):
    def validate(self):
        seen = set()
        for row in self.stops:
            if row.sequence in seen:
                frappe.throw(_("Row {0}: duplicate stop sequence {1}").format(row.idx, row.sequence))
            seen.add(row.sequence)
        self.stops.sort(key=lambda row: row.sequence)
        if self.default_door:
            require_storage_role(self.default_door, "Door", label=_("Default Door"))
            if frappe.db.get_value("Storage Bin", self.default_door, "warehouse") != self.origin_warehouse:
                frappe.throw(_("Default Door {0} does not belong to warehouse {1}").format(self.default_door, self.origin_warehouse))
