import frappe
from frappe import _
from frappe_wms.utils import require_role


def search_bins_for_assignment(warehouse, storage_type=None, storage_section=None, aisle=None, rack=None):
    require_role("WMS Supervisor", "WMS Master Data")
    filters = {"warehouse": warehouse, "active": 1}
    if storage_type: filters["storage_type"] = storage_type
    if storage_section: filters["storage_section"] = storage_section
    if aisle: filters["aisle"] = aisle
    if rack: filters["rack"] = rack
    return frappe.get_list("Storage Bin", filters=filters,
        fields=["name", "bin_code", "storage_type", "storage_section", "activity_area", "aisle", "rack"],
        order_by="sequence asc, bin_code asc", limit=500)


def mass_assign_activity_area(bin_names, activity_area):
    # The "mass maintenance transaction" the RF/desk side otherwise lacks: apply one Activity
    # Area to many Storage Bins in a single call instead of editing each bin's form. Same
    # filter-then-select-then-apply shape as every other Monitor bulk action (Slotting's
    # rearrangement-task generation, Alerts' bulk-approve).
    require_role("WMS Supervisor", "WMS Master Data")
    if isinstance(bin_names, str): bin_names = frappe.parse_json(bin_names)
    if not bin_names: frappe.throw(_("Select at least one Storage Bin"))
    if activity_area and not frappe.db.exists("Activity Area", activity_area):
        frappe.throw(_("Activity Area {0} does not exist").format(activity_area))
    for bin_name in bin_names:
        frappe.db.set_value("Storage Bin", bin_name, "activity_area", activity_area or None)
    return {"updated": len(bin_names)}
