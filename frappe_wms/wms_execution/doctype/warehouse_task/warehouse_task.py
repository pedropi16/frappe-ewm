from frappe.model.document import Document
from frappe import _
import frappe

class WarehouseTask(Document):
    def before_insert(self):
        # Independent of the Warehouse Order sequence gate (services/warehouse_order.py) -
        # a chained Storage Process step's successor task naturally lands in a different
        # Warehouse Order/queue, so it needs its own hold until the predecessor confirms.
        # A Storage Process chain only ever creates the successor once its predecessor has
        # already confirmed, so it isn't held in that case - there's nothing left to wait on.
        if self.predecessor_task and self.status not in ("Cancelled", "Confirmed"):
            if frappe.db.get_value("Warehouse Task", self.predecessor_task, "status") != "Confirmed":
                self.status = "On Hold"
                self.blocking_reason = _("Waiting on predecessor task {0}").format(self.predecessor_task)
