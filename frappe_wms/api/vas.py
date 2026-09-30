import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.idempotency import run_once
from frappe_wms.services.vas import (
    list_open_vas_orders as _list_open_vas_orders,
    get_vas_order as _get_vas_order,
    complete_vas_activity as _complete_vas_activity,
    create_vas_order_from_packaging_spec as _create_vas_order_from_packaging_spec,
)


@frappe.whitelist()
@retry_on_deadlock
def list_open_vas_orders():
    return _list_open_vas_orders()


@frappe.whitelist()
@retry_on_deadlock
def get_vas_order(vas_order_name):
    return _get_vas_order(vas_order_name)


@frappe.whitelist()
@retry_on_deadlock
def complete_activity(vas_order_name, activity_row_name, remarks=None):
    return _complete_vas_activity(vas_order_name, activity_row_name, remarks)


@frappe.whitelist()
@retry_on_deadlock
def create_vas_order_from_packaging_spec(handling_unit, work_center_bin, idempotency_key=None):
    return run_once(idempotency_key, lambda: _create_vas_order_from_packaging_spec(handling_unit, work_center_bin))
