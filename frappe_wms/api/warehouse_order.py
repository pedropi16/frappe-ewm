import frappe
from frappe_wms.services.locks import guard
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.warehouse_order import (
    list_queues as _list_queues,
    list_my_warehouse_orders as _list_my_warehouse_orders,
    pull_next_warehouse_order as _pull_next_warehouse_order,
    warehouse_order_detail as _warehouse_order_detail,
    block_warehouse_order as _block_warehouse_order,
    resume_warehouse_order as _resume_warehouse_order,
)

# join_queue/leave_queue (services/warehouse_order.py) are deliberately not exposed here: queue
# eligibility is a Resource Group setting a supervisor manages (Warehouse Queue.resource_group,
# or pinning one Resource to one queue straight from its desk form), never something an operator
# self-assigns from the scanner app - list_queues below stays read-only information for them.

@frappe.whitelist()
@retry_on_deadlock
def list_queues(warehouse=None, activity=None):
    return _list_queues(warehouse, activity)

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
@guard("Warehouse Order", "wo_name")
def block_warehouse_order(wo_name, reason=None):
    return _block_warehouse_order(wo_name, reason)

@frappe.whitelist()
@retry_on_deadlock
@guard("Warehouse Order", "wo_name")
def resume_warehouse_order(wo_name):
    return _resume_warehouse_order(wo_name)
