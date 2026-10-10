from frappe.model.document import Document


class OutboundDelivery(Document):
    def on_submit(self):
        # Draft is the document before submit; a submitted delivery that nothing has touched yet is Open (the other statuses follow the work done on it)
        if self.status == "Draft": self.db_set("status", "Open")
