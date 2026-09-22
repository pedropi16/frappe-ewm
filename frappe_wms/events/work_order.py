import frappe
from frappe.utils import flt
from frappe_wms.services.determination import _preferred_storage_type
from frappe_wms.services.replenishment import _create_replenishment_request

def _production_supply_bin(warehouse):
    # A dedicated storage_role ("Production Supply") rather than routing through the generic
    # Internal Move Bin Determination Rule engine - that engine has no way to distinguish
    # "stage this for production" from an ordinary internal move, and a warehouse's existing
    # Internal Move rule (if any) would otherwise silently misroute staging into bulk storage.
    storage_types = frappe.get_all("Storage Type", filters={"warehouse": warehouse, "storage_role": "Production Supply", "active": 1}, pluck="name")
    if not storage_types: return None
    return frappe.db.get_value("Storage Bin", {"warehouse": warehouse, "storage_type": ["in", storage_types], "active": 1, "removal_blocked": 0}, "name", order_by="sequence asc")

def on_submit(doc, method=None):
    # Production supply, staging (materials -> production): for each required-material line
    # sourced from a WMS-managed warehouse, pull the still-outstanding quantity into a
    # Production-Supply-role bin, reusing the same replenishment mechanism as pick-face
    # replenishment. Best-effort throughout - a warehouse with no matching config for a given
    # item (no Production Supply bin configured, no source stock) just doesn't get an
    # auto-staged request; it never blocks the Work Order's own submission.
    for row in doc.required_items:
        wms_warehouse = frappe.db.get_value("WMS Warehouse", {"erpnext_warehouse": row.source_warehouse}, "name")
        if not wms_warehouse: continue
        if not frappe.db.exists("WMS Product", {"item": row.item_code, "warehouse_managed": 1}): continue
        remaining = flt(row.required_qty) - flt(row.transferred_qty)
        if remaining <= 0: continue
        source_storage_type = _preferred_storage_type(row.item_code, wms_warehouse)
        if not source_storage_type: continue
        destination_bin = _production_supply_bin(wms_warehouse)
        if not destination_bin: continue
        try:
            _create_replenishment_request(wms_warehouse, row.item_code, destination_bin, "AVAILABLE", remaining, source_storage_type,
                reference_doctype="Work Order", reference_name=doc.name, reference_line=row.name, priority="High")
        except frappe.ValidationError:
            continue
