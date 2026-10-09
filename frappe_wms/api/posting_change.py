import frappe
from frappe_wms.services.locks import guard
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.posting_change import (
    post_posting_change as _post_posting_change,
    cancel_posting_change as _cancel_posting_change,
)


@frappe.whitelist()
@retry_on_deadlock
@guard("WMS Posting Change", "name")
def post_posting_change(name):
    return _post_posting_change(name)


@frappe.whitelist()
@retry_on_deadlock
@guard("WMS Posting Change", "name")
def cancel_posting_change(name):
    return _cancel_posting_change(name)
