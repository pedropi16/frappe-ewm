import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.inventory_count import snapshot_count as _snapshot_count, record_counts as _record_counts, post_count as _post_count, list_open_counts as _list_open_counts, request_recount as _request_recount, approve_variance as _approve_variance, analyze_differences as _analyze_differences, add_found_line as _add_found_line, cancel_count as _cancel_count, pull_next_count as _pull_next_count, find_count_for as _find_count_for
from frappe_wms.services.quality import complete_inspection as _complete_inspection, list_open_inspections as _list_open_inspections
from frappe_wms.services.replenishment import check_replenishment_needs as _check_replenishment_needs, request_direct_replenishment as _request_direct_replenishment
from frappe_wms.utils import parse_json

@frappe.whitelist()
@retry_on_deadlock
def list_open_counts():
    return _list_open_counts()

@frappe.whitelist()
@retry_on_deadlock
def list_open_inspections():
    return _list_open_inspections()

@frappe.whitelist()
@retry_on_deadlock
def pull_next_count():
    return _pull_next_count()

@frappe.whitelist()
@retry_on_deadlock
def find_count_for(reference):
    return _find_count_for(reference)

@frappe.whitelist()
@retry_on_deadlock
def snapshot_count(count_name):
    return _snapshot_count(count_name)

@frappe.whitelist()
@retry_on_deadlock
def record_counts(count_name, counted_quantities):
    return _record_counts(count_name, parse_json(counted_quantities, "counted_quantities"))

@frappe.whitelist()
@retry_on_deadlock
def post_count(count_name):
    return _post_count(count_name)

@frappe.whitelist()
@retry_on_deadlock
def request_recount(count_name):
    return _request_recount(count_name)

@frappe.whitelist()
@retry_on_deadlock
def add_found_line(count_name, product, storage_bin, stock_type, quantity, batch_no=None, serial_no=None, handling_unit=None, stock_uom=None):
    return _add_found_line(count_name, product, storage_bin, stock_type, quantity, batch_no, serial_no, handling_unit, stock_uom)

@frappe.whitelist()
@retry_on_deadlock
def cancel_count(count_name):
    return _cancel_count(count_name)

@frappe.whitelist()
@retry_on_deadlock
def approve_variance(count_name, remarks=None):
    return _approve_variance(count_name, remarks)

@frappe.whitelist()
@retry_on_deadlock
def analyze_differences(warehouse, from_date=None, to_date=None, product=None):
    return _analyze_differences(warehouse, from_date, to_date, product)

@frappe.whitelist()
def difference_summary(warehouse, from_date=None, to_date=None, product=None):
    from frappe_wms.services.difference import difference_summary as _summary
    return _summary(warehouse, from_date, to_date, product)


@frappe.whitelist()
@retry_on_deadlock
def complete_inspection(inspection_name, passed_quantity=None, failed_quantity=None, decisions=None):
    return _complete_inspection(inspection_name, passed_quantity, failed_quantity, parse_json(decisions, "decisions") if decisions else None)

@frappe.whitelist()
@retry_on_deadlock
def record_sample_result(inspection_name, sample_name, result, remarks=None):
    from frappe_wms.services.quality import record_sample_result as _record
    return _record(inspection_name, sample_name, result, remarks)

@frappe.whitelist()
@retry_on_deadlock
def check_replenishment_needs():
    return _check_replenishment_needs()

@frappe.whitelist()
@retry_on_deadlock
def request_direct_replenishment(warehouse, product, storage_bin, stock_type, quantity, source_storage_type):
    return _request_direct_replenishment(warehouse, product, storage_bin, stock_type, quantity, source_storage_type)
