import frappe
from frappe_wms.services.handling_unit import (
    list_handling_units as _list_handling_units,
    create_handling_unit as _create_handling_unit,
    nest_handling_unit as _nest_handling_unit,
    unnest_handling_unit as _unnest_handling_unit,
    set_handling_unit_blocked as _set_handling_unit_blocked,
    handling_unit_detail as _handling_unit_detail,
    recycle_handling_unit as _recycle_handling_unit,
)

@frappe.whitelist()
def list_handling_units(search=None, warehouse=None, storage_bin=None):
    return _list_handling_units(search, warehouse, storage_bin)

@frappe.whitelist()
def create_handling_unit(hu_type, hu_number=None, storage_bin=None, parent_hu=None, warehouse=None):
    return _create_handling_unit(hu_number, hu_type, storage_bin, parent_hu, warehouse)

@frappe.whitelist()
def nest_handling_unit(hu_name, parent_hu):
    return _nest_handling_unit(hu_name, parent_hu)

@frappe.whitelist()
def unnest_handling_unit(hu_name):
    return _unnest_handling_unit(hu_name)

@frappe.whitelist()
def block_handling_unit(hu_name, reason_code=None, remarks=None):
    return _set_handling_unit_blocked(hu_name, True, reason_code, remarks)

@frappe.whitelist()
def unblock_handling_unit(hu_name):
    return _set_handling_unit_blocked(hu_name, False)

@frappe.whitelist()
def handling_unit_detail(hu_name):
    return _handling_unit_detail(hu_name)

@frappe.whitelist()
def recycle_handling_unit(hu_name):
    return _recycle_handling_unit(hu_name)
