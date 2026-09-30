import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.warehouse_order import (
    list_queues as _list_queues,
    join_queue as _join_queue,
    leave_queue as _leave_queue,
    list_my_warehouse_orders as _list_my_warehouse_orders,
    pull_next_warehouse_order as _pull_next_warehouse_order,
    warehouse_order_detail as _warehouse_order_detail,
    block_warehouse_order as _block_warehouse_order,
    resume_warehouse_order as _resume_warehouse_order,
)

@frappe.whitelist()
@retry_on_deadlock
def list_queues(warehouse=None, activity=None):
    return _list_queues(warehouse, activity)

@frappe.whitelist()
@retry_on_deadlock
def join_queue(queue_name):
    return _join_queue(queue_name)

@frappe.whitelist()
@retry_on_deadlock
def leave_queue():
    return _leave_queue()

@frappe.whitelist()
@retry_on_deadlock
def my_warehouse_orders():
    return _list_my_warehouse_orders()

@frappe.whitelist()
@retry_on_deadlock
def pull_next_warehouse_order():
    return _pull_next_warehouse_order()

@frappe.whitelist()
@retry_on_deadlock
def warehouse_order_detail(wo_name):
    return _warehouse_order_detail(wo_name)

@frappe.whitelist()
@retry_on_deadlock
def block_warehouse_order(wo_name, reason=None):
    return _block_warehouse_order(wo_name, reason)

@frappe.whitelist()
@retry_on_deadlock
def resume_warehouse_order(wo_name):
    return _resume_warehouse_order(wo_name)
