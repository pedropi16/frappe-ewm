import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.receipt import create_putaway_requests, list_open_inbound_deliveries as _list_open_inbound_deliveries, create_and_submit_goods_receipt as _create_and_submit_goods_receipt, create_fg_receipt_from_work_order as _create_fg_receipt_from_work_order, create_return_inbound_delivery as _create_return_inbound_delivery, find_putaway_tasks as _find_putaway_tasks
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.services.procurement import create_inbound_delivery_from_purchase_order as _create_inbound_delivery_from_purchase_order
from frappe_wms.utils import parse_json, require_role
from frappe_wms.services.idempotency import run_once

RECEIVING_ROLES = ("WMS Operator", "WMS Receiver", "WMS Supervisor")

@frappe.whitelist()
@retry_on_deadlock
def create_putaway(receipt_name):
    require_role(*RECEIVING_ROLES)
    request_names = create_putaway_requests(receipt_name)
    from frappe_wms.services.task import plan_requests
    task_names, unplanned = plan_requests(request_names, batch_key=frappe.generate_hash(length=10))
    return {"warehouse_requests": request_names, "warehouse_tasks": task_names, "unplanned_requests": unplanned}

@frappe.whitelist()
@retry_on_deadlock
def find_putaway_tasks(reference):
    return _find_putaway_tasks(reference)

@frappe.whitelist()
@retry_on_deadlock
def create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse):
    require_role(*RECEIVING_ROLES)
    return _create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse)

@frappe.whitelist()
@retry_on_deadlock
def list_open_inbound_deliveries():
    require_role(*RECEIVING_ROLES)
    return _list_open_inbound_deliveries()

@frappe.whitelist()
@retry_on_deadlock
def receiving_worklist(inbound_delivery):
    require_role(*RECEIVING_ROLES)
    from frappe_wms.services.receipt import receiving_worklist as _receiving_worklist
    return _receiving_worklist(inbound_delivery)

@frappe.whitelist()
@retry_on_deadlock
def create_and_submit_goods_receipt(inbound_delivery, items, idempotency_key=None):
    return run_once(idempotency_key, lambda: _create_and_submit_goods_receipt(inbound_delivery, parse_json(items, "items")))

@frappe.whitelist()
@retry_on_deadlock
def create_fg_receipt_from_work_order(work_order_name, warehouse, quantity, handling_unit, hu_type=None, batch_no=None, serial_no=None, stock_type="AVAILABLE"):
    return _create_fg_receipt_from_work_order(work_order_name, warehouse, quantity, handling_unit, hu_type, batch_no, serial_no, stock_type)

@frappe.whitelist()
@retry_on_deadlock
def create_return_inbound_delivery(delivery_note, warehouse, item_type=None):
    return _create_return_inbound_delivery(delivery_note, warehouse, item_type)

@frappe.whitelist()
@retry_on_deadlock
def plan_cross_dock(inbound_delivery):
    from frappe_wms.services.cross_dock import plan_cross_dock as _plan_cross_dock
    return _plan_cross_dock(inbound_delivery)
