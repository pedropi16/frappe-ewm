import frappe
from frappe_wms.services.warehouse_order import (
    list_queues as _list_queues,
    join_queue as _join_queue,
    leave_queue as _leave_queue,
    list_my_warehouse_orders as _list_my_warehouse_orders,
    pull_next_warehouse_order as _pull_next_warehouse_order,
    warehouse_order_detail as _warehouse_order_detail,
)

@frappe.whitelist()
def list_queues(warehouse=None, activity=None):
    return _list_queues(warehouse, activity)

@frappe.whitelist()
def join_queue(queue_name):
    return _join_queue(queue_name)

@frappe.whitelist()
def leave_queue():
    return _leave_queue()

@frappe.whitelist()
def my_warehouse_orders():
    return _list_my_warehouse_orders()

@frappe.whitelist()
def pull_next_warehouse_order():
    return _pull_next_warehouse_order()

@frappe.whitelist()
def warehouse_order_detail(wo_name):
    return _warehouse_order_detail(wo_name)
