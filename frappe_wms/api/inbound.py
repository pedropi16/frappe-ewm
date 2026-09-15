import frappe
from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.services.procurement import create_inbound_delivery_from_purchase_order as _create_inbound_delivery_from_purchase_order

@frappe.whitelist()
def create_putaway(receipt_name):
    request_names = create_putaway_requests(receipt_name)
    task_names = [create_tasks_for_request(name) for name in request_names]
    return {"warehouse_requests": request_names, "warehouse_tasks": task_names}

@frappe.whitelist()
def create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse):
    return _create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse)
