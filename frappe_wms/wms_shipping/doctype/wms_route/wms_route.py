import frappe
from frappe import _
from frappe.model.document import Document

class WMSRoute(Document):
    def validate(self):
        seen = set()
        for row in self.stops:
            if row.sequence in seen:
                frappe.throw(_("Row {0}: duplicate stop sequence {1}").format(row.idx, row.sequence))
            seen.add(row.sequence)
        self.stops.sort(key=lambda row: row.sequence)
