import frappe
from frappe_wms.services.inventory_count import snapshot_count as _snapshot_count, record_counts as _record_counts, post_count as _post_count, list_open_counts as _list_open_counts
from frappe_wms.services.quality import complete_inspection as _complete_inspection, list_open_inspections as _list_open_inspections
from frappe_wms.services.replenishment import check_replenishment_needs as _check_replenishment_needs, request_direct_replenishment as _request_direct_replenishment
from frappe_wms.utils import parse_json

@frappe.whitelist()
def list_open_counts():
    return _list_open_counts()

@frappe.whitelist()
def list_open_inspections():
    return _list_open_inspections()

@frappe.whitelist()
def snapshot_count(count_name):
    return _snapshot_count(count_name)

@frappe.whitelist()
def record_counts(count_name, counted_quantities):
    return _record_counts(count_name, parse_json(counted_quantities, "counted_quantities"))

@frappe.whitelist()
def post_count(count_name):
    return _post_count(count_name)

@frappe.whitelist()
def complete_inspection(inspection_name, passed_quantity=None, failed_quantity=None):
    return _complete_inspection(inspection_name, passed_quantity, failed_quantity)

@frappe.whitelist()
def check_replenishment_needs():
    return _check_replenishment_needs()

@frappe.whitelist()
def request_direct_replenishment(warehouse, product, storage_bin, stock_type, quantity, source_storage_type):
    return _request_direct_replenishment(warehouse, product, storage_bin, stock_type, quantity, source_storage_type)
