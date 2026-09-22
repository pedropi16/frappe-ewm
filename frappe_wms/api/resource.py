import frappe
from frappe_wms.services.resource import (
    list_available_resources as _list_available_resources,
    log_on as _log_on,
    log_off as _log_off,
    kick as _kick,
    list_available_work_centers as _list_available_work_centers,
    log_on_work_center as _log_on_work_center,
    log_off_work_center as _log_off_work_center,
)

@frappe.whitelist()
def list_available_resources(warehouse=None):
    return _list_available_resources(warehouse)

@frappe.whitelist()
def log_on(resource_code):
    return _log_on(resource_code)

@frappe.whitelist()
def log_off(resource_code=None):
    return _log_off(resource_code)

@frappe.whitelist()
def kick(resource_code, reason=None):
    return _kick(resource_code, reason)

@frappe.whitelist()
def list_available_work_centers(warehouse=None):
    return _list_available_work_centers(warehouse)

@frappe.whitelist()
def log_on_work_center(work_center_code):
    return _log_on_work_center(work_center_code)

@frappe.whitelist()
def log_off_work_center():
    return _log_off_work_center()
