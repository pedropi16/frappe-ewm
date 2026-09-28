import frappe
from frappe_wms.services.posting_change import (
    post_posting_change as _post_posting_change,
    cancel_posting_change as _cancel_posting_change,
)


@frappe.whitelist()
def post_posting_change(name):
    return _post_posting_change(name)


@frappe.whitelist()
def cancel_posting_change(name):
    return _cancel_posting_change(name)
