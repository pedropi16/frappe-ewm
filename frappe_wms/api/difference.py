import frappe
from frappe_wms.services.difference import (
    list_open_differences as _list_open_differences,
    clear_over_difference as _clear_over_difference,
    clear_short_difference as _clear_short_difference,
)


@frappe.whitelist()
def list_open_differences(warehouse=None, direction=None):
    return _list_open_differences(warehouse, direction)


@frappe.whitelist()
def clear_over_difference(name, destination_bin, destination_hu=None):
    return _clear_over_difference(name, destination_bin, destination_hu)


@frappe.whitelist()
def clear_short_difference(name, remarks=None):
    return _clear_short_difference(name, remarks)
