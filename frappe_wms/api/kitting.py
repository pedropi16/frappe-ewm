import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services import kitting


@frappe.whitelist()
@retry_on_deadlock
def list_open_kitting_orders():
    return kitting.list_open_kitting_orders()


@frappe.whitelist()
def get_kitting_order(kitting_order_name):
    return kitting.get_kitting_order(kitting_order_name)


@frappe.whitelist()
@retry_on_deadlock
def create_kitting_order(kit_item, bom, warehouse, work_center_bin, quantity, direction, putaway_output=None):
    return kitting.create_kitting_order(kit_item, bom, warehouse, work_center_bin, quantity, direction, putaway_output=putaway_output)


@frappe.whitelist()
@retry_on_deadlock
def stage_kitting_components(kitting_order_name):
    return kitting.stage_kitting_components(kitting_order_name)


@frappe.whitelist()
@retry_on_deadlock
def complete_kitting_order(kitting_order_name, destination_hu=None):
    return kitting.complete_kitting_order(kitting_order_name, destination_hu=destination_hu)


@frappe.whitelist()
@retry_on_deadlock
def cancel_kitting_order(kitting_order_name):
    return kitting.cancel_kitting_order(kitting_order_name)
