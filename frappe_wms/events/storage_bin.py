import frappe
from frappe import _

def validate_storage_bin(doc, method=None):
    if doc.storage_type:
        storage_type = frappe.get_cached_doc("Storage Type", doc.storage_type)
        if storage_type.warehouse != doc.warehouse:
            frappe.throw(_("Storage type and storage bin must belong to the same warehouse"))
    if doc.get("storage_group") and frappe.db.get_value("Storage Group", doc.storage_group, "storage_type") != doc.storage_type:
        frappe.throw(_("Storage group {0} does not belong to storage type {1}").format(doc.storage_group, doc.storage_type))
    if (doc.putaway_blocked or doc.removal_blocked or doc.inventory_blocked) and not doc.get("block_reason") and frappe.get_cached_value("WMS Settings", "WMS Settings", "require_block_reason"):
        frappe.throw(_("Choose a block reason for a blocked bin"))
    if doc.get("block_reason") and frappe.db.get_value("WMS Block Reason", doc.block_reason, "applies_to") == "Handling Unit":
        frappe.throw(_("Block reason {0} is for Handling Units").format(doc.block_reason))
    if doc.maximum_hus and doc.maximum_hus < 0:
        frappe.throw(_("Maximum HUs cannot be negative"))
