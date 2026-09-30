"""Packing Station (SAP EWM packing work center) - see services/packing_station.py."""
import frappe

from frappe_wms.services import packing_station as ps
from frappe_wms.services.concurrency import retry_on_deadlock


@frappe.whitelist()
@retry_on_deadlock
def list_work_centers(warehouse=None):
    return ps.list_work_centers(warehouse)


@frappe.whitelist()
@retry_on_deadlock
def station_overview(work_center):
    return ps.station_overview(work_center)


@frappe.whitelist()
@retry_on_deadlock
def pack_product(work_center, product, quantity, destination_hu, source_hu=None, stock_type=None, batch_no=None, serial_no=None,
                 outbound_delivery=None, idempotency_key=None):
    return ps.pack_product(work_center, product, quantity, destination_hu, source_hu, stock_type, batch_no, serial_no,
                           outbound_delivery, idempotency_key)


@frappe.whitelist()
@retry_on_deadlock
def pack_all(work_center, source_hu, destination_hu, outbound_delivery=None, idempotency_key=None):
    return ps.pack_all(work_center, source_hu, destination_hu, outbound_delivery, idempotency_key)


@frappe.whitelist()
@retry_on_deadlock
def pack_hu(work_center, hu_name, destination_hu, outbound_delivery=None):
    return ps.pack_hu(work_center, hu_name, destination_hu, outbound_delivery)


@frappe.whitelist()
@retry_on_deadlock
def unpack_hu(work_center, hu_name):
    return ps.unpack_hu(work_center, hu_name)


@frappe.whitelist()
@retry_on_deadlock
def create_hu(work_center, hu_type, hu_number=None, outbound_delivery=None):
    return ps.create_station_hu(work_center, hu_type, hu_number, outbound_delivery)


@frappe.whitelist()
@retry_on_deadlock
def close_hu(work_center, hu_name, gross_weight=None, move_to_bin=None):
    return ps.close_hu(work_center, hu_name, gross_weight, move_to_bin)


@frappe.whitelist()
@retry_on_deadlock
def reopen_hu(work_center, hu_name):
    return ps.reopen_hu(work_center, hu_name)
