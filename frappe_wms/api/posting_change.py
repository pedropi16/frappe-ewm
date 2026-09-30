import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.posting_change import (
    post_posting_change as _post_posting_change,
    cancel_posting_change as _cancel_posting_change,
)


@frappe.whitelist()
@retry_on_deadlock
def post_posting_change(name):
    return _post_posting_change(name)


@frappe.whitelist()
@retry_on_deadlock
def cancel_posting_change(name):
    return _cancel_posting_change(name)
