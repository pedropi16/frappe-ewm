import frappe
from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.task import create_tasks_for_request

@frappe.whitelist()
def create_putaway(receipt_name):
    request_names = create_putaway_requests(receipt_name)
    task_names = [create_tasks_for_request(name) for name in request_names]
    return {"warehouse_requests": request_names, "warehouse_tasks": task_names}
