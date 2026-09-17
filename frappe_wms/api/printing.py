import frappe
from frappe_wms.services.printing import list_queued_spools as _list_queued_spools, mark_printed as _mark_printed, mark_failed as _mark_failed

@frappe.whitelist()
def list_queued_spools(warehouse=None, output_device=None):
    return _list_queued_spools(warehouse, output_device)

@frappe.whitelist()
def mark_printed(spool_name):
    return _mark_printed(spool_name)

@frappe.whitelist()
def mark_failed(spool_name, reason=None):
    return _mark_failed(spool_name, reason)
