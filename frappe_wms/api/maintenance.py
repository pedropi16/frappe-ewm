import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.stock import rebuild_balances as _rebuild_balances
from frappe_wms.utils import require_role

@frappe.whitelist()
@retry_on_deadlock
def rebuild_balances(warehouse=None, product=None):
    require_role("WMS Administrator")
    return _rebuild_balances(warehouse, product)
