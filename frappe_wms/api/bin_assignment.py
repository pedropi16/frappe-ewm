import frappe
from frappe_wms.services.bin_assignment import (
    search_bins_for_assignment as _search_bins_for_assignment,
    mass_assign_activity_area as _mass_assign_activity_area,
)


@frappe.whitelist()
def search_bins_for_assignment(warehouse, storage_type=None, storage_section=None, aisle=None, rack=None):
    return _search_bins_for_assignment(warehouse, storage_type, storage_section, aisle, rack)


@frappe.whitelist()
def mass_assign_activity_area(bin_names, activity_area=None):
    return _mass_assign_activity_area(bin_names, activity_area)
