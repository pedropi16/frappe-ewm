import frappe
from frappe_wms.services.allocation import allocate_delivery as _allocate_delivery
from frappe_wms.services.task import create_pick_tasks as _create_pick_tasks
from frappe_wms.services.picking import release_wave as _release_wave
from frappe_wms.services.sales import create_outbound_delivery_from_sales_order as _create_outbound_delivery_from_sales_order

@frappe.whitelist()
def allocate_delivery(delivery_name):
    return _allocate_delivery(delivery_name)

@frappe.whitelist()
def create_pick_tasks(delivery_name, strategy="Single Order"):
    return _create_pick_tasks(delivery_name, strategy)

@frappe.whitelist()
def release_wave(wave_name):
    return _release_wave(wave_name)

@frappe.whitelist()
def create_outbound_delivery_from_sales_order(sales_order_name, warehouse):
    return _create_outbound_delivery_from_sales_order(sales_order_name, warehouse)
