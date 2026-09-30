import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.allocation import allocate_delivery as _allocate_delivery
from frappe_wms.services.task import create_pick_tasks as _create_pick_tasks
from frappe_wms.services.picking import (
    release_wave as _release_wave,
    list_awaiting_release as _list_awaiting_release,
    release_delivery_for_picking as _release_delivery_for_picking,
    find_pick_tasks as _find_pick_tasks,
)
from frappe_wms.services.sales import create_outbound_delivery_from_sales_order as _create_outbound_delivery_from_sales_order, create_return_outbound_delivery as _create_return_outbound_delivery
from frappe_wms.services.issue import (
    list_ready_to_ship as _list_ready_to_ship,
    create_and_submit_goods_issue as _create_and_submit_goods_issue,
    post_goods_issue_for_delivery as _post_goods_issue_for_delivery,
)
from frappe_wms.utils import parse_json, require_role
from frappe_wms.services.idempotency import run_once

OUTBOUND_ROLES = ("WMS Operator", "WMS Picker", "WMS Loader", "WMS Supervisor")

@frappe.whitelist()
@retry_on_deadlock
def allocate_delivery(delivery_name):
    return _allocate_delivery(delivery_name)

@frappe.whitelist()
@retry_on_deadlock
def create_pick_tasks(delivery_name, strategy="Single Order"):
    return _create_pick_tasks(delivery_name, strategy)

@frappe.whitelist()
@retry_on_deadlock
def release_wave(wave_name):
    return _release_wave(wave_name)

@frappe.whitelist()
@retry_on_deadlock
def list_awaiting_release():
    return _list_awaiting_release()

@frappe.whitelist()
@retry_on_deadlock
def release_delivery_for_picking(delivery_name, strategy="Single Order"):
    return _release_delivery_for_picking(delivery_name, strategy)

@frappe.whitelist()
@retry_on_deadlock
def find_pick_tasks(reference):
    return _find_pick_tasks(reference)

@frappe.whitelist()
@retry_on_deadlock
def create_outbound_delivery_from_sales_order(sales_order_name, warehouse):
    require_role(*OUTBOUND_ROLES)
    return _create_outbound_delivery_from_sales_order(sales_order_name, warehouse)

@frappe.whitelist()
@retry_on_deadlock
def list_ready_to_ship():
    require_role(*OUTBOUND_ROLES)
    return _list_ready_to_ship()

@frappe.whitelist()
@retry_on_deadlock
def create_and_submit_goods_issue(outbound_delivery, items, idempotency_key=None):
    return run_once(idempotency_key, lambda: _create_and_submit_goods_issue(outbound_delivery, parse_json(items, "items")))

@frappe.whitelist()
@retry_on_deadlock
def post_goods_issue_for_delivery(delivery_name):
    return _post_goods_issue_for_delivery(delivery_name)

@frappe.whitelist()
@retry_on_deadlock
def create_return_outbound_delivery(purchase_receipt, warehouse, stock_type="DAMAGED"):
    require_role(*OUTBOUND_ROLES)
    return _create_return_outbound_delivery(purchase_receipt, warehouse, stock_type)
