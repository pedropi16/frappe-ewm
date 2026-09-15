import frappe
from frappe import _

def validate_storage_bin(doc, method=None):
    if doc.storage_type:
        storage_type = frappe.get_cached_doc("Storage Type", doc.storage_type)
        if storage_type.warehouse != doc.warehouse:
            frappe.throw(_("Storage type and storage bin must belong to the same warehouse"))
    if doc.maximum_hus and doc.maximum_hus < 0:
        frappe.throw(_("Maximum HUs cannot be negative"))
