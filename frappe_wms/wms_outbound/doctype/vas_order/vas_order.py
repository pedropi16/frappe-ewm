from frappe.model.document import Document
from frappe.utils import now_datetime

class VASOrder(Document):
    def before_insert(self):
        # Same "creation time" approximation as Warehouse Task.started_at (P3) - not the
        # moment an operator actually began the step, but a real, honest first cut.
        for row in self.activities:
            row.started_at = row.started_at or now_datetime()
