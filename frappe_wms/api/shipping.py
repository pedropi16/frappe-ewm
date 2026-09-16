import frappe
from frappe_wms.services.shipping import (
    create_shipment as _create_shipment,
    list_loadable_shipments as _list_loadable_shipments,
    confirm_hu_loaded as _confirm_hu_loaded,
    depart_shipment as _depart_shipment,
    complete_shipment as _complete_shipment,
)
from frappe_wms.utils import parse_json

@frappe.whitelist()
def create_shipment(warehouse, outbound_deliveries, carrier=None, route=None, vehicle_registration=None, driver_name=None):
    return _create_shipment(warehouse, parse_json(outbound_deliveries), carrier, route, vehicle_registration, driver_name)

@frappe.whitelist()
def list_loadable_shipments():
    return _list_loadable_shipments()

@frappe.whitelist()
def confirm_hu_loaded(shipment_name, hu_name):
    return _confirm_hu_loaded(shipment_name, hu_name)

@frappe.whitelist()
def depart_shipment(shipment_name):
    return _depart_shipment(shipment_name)

@frappe.whitelist()
def complete_shipment(shipment_name):
    return _complete_shipment(shipment_name)
