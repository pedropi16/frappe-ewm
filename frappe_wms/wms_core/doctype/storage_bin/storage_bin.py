from frappe.model.document import Document
from frappe_wms.utils import generate_check_digits

class StorageBin(Document):
    def before_insert(self):
        if not self.check_digits:
            self.check_digits = generate_check_digits()

    def after_insert(self):
        from frappe_wms.services.printing import create_print_spool
        create_print_spool("Storage Bin", self.name, "Bin Created", self.warehouse)
