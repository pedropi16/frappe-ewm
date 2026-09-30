import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.printing import list_queued_spools as _list_queued_spools, mark_printed as _mark_printed, mark_failed as _mark_failed

@frappe.whitelist()
@retry_on_deadlock
def list_queued_spools(warehouse=None, output_device=None):
    return _list_queued_spools(warehouse, output_device)

@frappe.whitelist()
@retry_on_deadlock
def mark_printed(spool_name):
    return _mark_printed(spool_name)

@frappe.whitelist()
@retry_on_deadlock
def mark_failed(spool_name, reason=None):
    return _mark_failed(spool_name, reason)


@frappe.whitelist()
@retry_on_deadlock
def request_print(reference_doctype, reference_name, output_device=None, print_format=None):
    from frappe_wms.services.printing import request_print as _request_print
    return _request_print(reference_doctype, reference_name, output_device, print_format)

@frappe.whitelist()
@retry_on_deadlock
def agent_claim(output_device, limit=10):
    from frappe_wms.services.printing import agent_claim as _agent_claim
    return _agent_claim(output_device, limit)

@frappe.whitelist()
@retry_on_deadlock
def requeue(spool_name):
    from frappe_wms.services.printing import requeue as _requeue
    return _requeue(spool_name)
