from frappe.model.document import Document


class PackagingSpec(Document):
    def autoname(self):
        # The generic spec of an item is named by the item (as it always was); a conditional one by the item and its customer/supplier.
        self.name = "-".join(p for p in (self.item, self.customer or self.supplier) if p)
