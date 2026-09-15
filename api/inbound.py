import frappe
from frappe_wms.services.receipt import create_putaway_requests

@frappe.whitelist()
def create_putaway(receipt_name):
    return create_putaway_requests(receipt_name)
