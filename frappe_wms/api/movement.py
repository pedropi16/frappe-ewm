import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.movement import close_movement as _close_movement
from frappe_wms.services.idempotency import run_once


@frappe.whitelist()
@retry_on_deadlock
def close_movement(hu_name, device=None, idempotency_key=None):
    return run_once(idempotency_key, lambda: _close_movement(hu_name, device))
