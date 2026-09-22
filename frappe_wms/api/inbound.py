import frappe
from frappe_wms.services.receipt import create_putaway_requests, list_open_inbound_deliveries as _list_open_inbound_deliveries, create_and_submit_goods_receipt as _create_and_submit_goods_receipt, create_fg_receipt_from_work_order as _create_fg_receipt_from_work_order
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.services.procurement import create_inbound_delivery_from_purchase_order as _create_inbound_delivery_from_purchase_order
from frappe_wms.utils import parse_json

@frappe.whitelist()
def create_putaway(receipt_name):
    request_names = create_putaway_requests(receipt_name)
    batch_key = frappe.generate_hash(length=10)
    task_names = [create_tasks_for_request(name, batch_key=batch_key) for name in request_names]
    return {"warehouse_requests": request_names, "warehouse_tasks": task_names}

@frappe.whitelist()
def create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse):
    return _create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse)

@frappe.whitelist()
def list_open_inbound_deliveries():
    return _list_open_inbound_deliveries()

@frappe.whitelist()
def create_and_submit_goods_receipt(inbound_delivery, items):
    return _create_and_submit_goods_receipt(inbound_delivery, parse_json(items, "items"))

@frappe.whitelist()
def create_fg_receipt_from_work_order(work_order_name, warehouse, quantity, handling_unit, hu_type=None, batch_no=None, serial_no=None, stock_type="AVAILABLE"):
    return _create_fg_receipt_from_work_order(work_order_name, warehouse, quantity, handling_unit, hu_type, batch_no, serial_no, stock_type)
