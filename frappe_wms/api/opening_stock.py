import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.opening_stock import (
    post_opening_stock_load as _post_opening_stock_load,
    cancel_opening_stock_load as _cancel_opening_stock_load,
    list_draft_loads as _list_draft_loads,
)


@frappe.whitelist()
@retry_on_deadlock
def post_opening_stock_load(name):
    return _post_opening_stock_load(name)


@frappe.whitelist()
@retry_on_deadlock
def cancel_opening_stock_load(name):
    return _cancel_opening_stock_load(name)


@frappe.whitelist()
@retry_on_deadlock
def list_draft_loads(warehouse=None):
    return _list_draft_loads(warehouse)
