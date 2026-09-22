import frappe
from frappe_wms.services.kitting import create_kitting_order as _create_kitting_order, complete_kitting_order as _complete_kitting_order

@frappe.whitelist()
def create_kitting_order(kit_item, bom, warehouse, work_center_bin, quantity, direction):
    return _create_kitting_order(kit_item, bom, warehouse, work_center_bin, quantity, direction)

@frappe.whitelist()
def complete_kitting_order(kitting_order_name):
    return _complete_kitting_order(kitting_order_name)
