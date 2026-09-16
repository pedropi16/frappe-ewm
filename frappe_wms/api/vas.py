import frappe
from frappe_wms.services.vas import (
    list_open_vas_orders as _list_open_vas_orders,
    get_vas_order as _get_vas_order,
    complete_vas_activity as _complete_vas_activity,
)


@frappe.whitelist()
def list_open_vas_orders():
    return _list_open_vas_orders()


@frappe.whitelist()
def get_vas_order(vas_order_name):
    return _get_vas_order(vas_order_name)


@frappe.whitelist()
def complete_activity(vas_order_name, activity_row_name, remarks=None):
    return _complete_vas_activity(vas_order_name, activity_row_name, remarks)
