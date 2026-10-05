"""Production supply through a Production Supply Area (PSA), the SAP EWM way: a Material Request (the production
material request) pulls material from the warehouse into one free location of the PSA's deconsolidation Work Center -
one location per request - and once every line has arrived a Consolidation Group gathers it onto one Handling Unit in
the PSA's supply bin: the delivery to production, where it is consumed as orders start or complete.

Reuses the replenishment pipeline (storage -> location) and the Consolidation Group (location -> supply bin); this
module only wires them to a Material Request.
"""
import frappe
from frappe import _
from frappe.utils import flt

from frappe_wms.services.determination import _preferred_storage_type
from frappe_wms.services.replenishment import _create_replenishment_request


def resolve_psa(wms_warehouse, production_warehouse):
    return frappe.db.get_value("Production Supply Area", {"warehouse": wms_warehouse, "production_warehouse": production_warehouse, "active": 1},
        ["name", "deconsolidation_work_center", "supply_bin"], as_dict=True)


def psa_for_production_warehouse(production_warehouse):
    return frappe.db.exists("Production Supply Area", {"production_warehouse": production_warehouse, "active": 1})


def _free_location(work_center):
    # Locked so two Material Requests submitted at once never get the same location. Free = holds no stock and is
    # not the collection point of another open group.
    frappe.db.get_value("Work Center", work_center, "name", for_update=True)
    taken = set(frappe.get_all("Consolidation Group", filters={"status": ["in", ["Draft", "Open"]], "deconsolidation_bin": ["is", "set"]}, pluck="deconsolidation_bin"))
    for loc in frappe.get_all("Work Center Location", filters={"parent": work_center, "parenttype": "Work Center"}, pluck="storage_bin", order_by="idx asc"):
        if loc in taken or not frappe.db.get_value("Storage Bin", loc, "active"): continue
        if frappe.db.exists("WMS Stock Balance", {"storage_bin": loc, "quantity": [">", 0]}): continue
        return loc
    return None


def _group_for(material_request, psa, warehouse):
    """The order's group, with its location in the deconsolidation work center (assigned once, on first use)."""
    name = frappe.db.get_value("Consolidation Group", {"material_request": material_request, "status": ["!=", "Cancelled"]})
    if name: return name, frappe.db.get_value("Consolidation Group", name, "deconsolidation_bin")
    location = _free_location(psa.deconsolidation_work_center)
    if not location: frappe.throw(_("No free location in deconsolidation work center {0}").format(psa.deconsolidation_work_center))
    group = frappe.get_doc({"doctype": "Consolidation Group", "warehouse": warehouse, "staging_bin": psa.supply_bin, "priority": "High",
        "status": "Draft", "gather_status": "Not Started", "production_supply_area": psa.name, "material_request": material_request, "deconsolidation_bin": location})
    group.insert(ignore_permissions=True)
    return group.name, location


def on_material_request_submit(doc):
    """A Material Transfer into a PSA's production warehouse: request each line from the WMS-managed source warehouse
    into the order's location in the PSA's deconsolidation work center. Best effort like Work Order staging - a line with no stock or config is noted on
    the request instead of blocking its submission."""
    if doc.material_request_type != "Material Transfer": return
    for row in doc.items:
        source = row.get("from_warehouse") or doc.get("set_from_warehouse")
        wms_warehouse = frappe.db.get_value("WMS Warehouse", {"erpnext_warehouse": source}, "name") if source else None
        psa = resolve_psa(wms_warehouse, row.warehouse) if wms_warehouse else None
        if not psa or not frappe.db.exists("WMS Product", {"item": row.item_code, "warehouse_managed": 1}): continue
        storage_type = _preferred_storage_type(row.item_code, wms_warehouse)
        try:
            if not storage_type: frappe.throw(_("{0} has no preferred storage type in {1}").format(row.item_code, wms_warehouse))
            _, location = _group_for(doc.name, psa, wms_warehouse)
            _create_replenishment_request(wms_warehouse, row.item_code, location, "AVAILABLE", flt(row.stock_qty) or flt(row.qty), storage_type,
                reference_doctype="Material Request", reference_name=doc.name, reference_line=row.name, priority="High")
        except frappe.ValidationError as e:
            frappe.clear_messages()
            doc.add_comment("Comment", _("Not supplied by the WMS for {0}: {1}").format(row.item_code, e))


def on_request_completed(request):
    """A supply request reached the order's location. Join it to the order's group; when it was the last open one,
    gather the whole set onto one HU in the supply bin."""
    from frappe_wms.services.consolidation import _already_joined, add_consolidation_line, gather_consolidation_group, set_consolidation_target_hu
    group_name = frappe.db.get_value("Consolidation Group", {"material_request": request.reference_name, "status": ["!=", "Cancelled"]})
    if not group_name or _already_joined("Warehouse Request", request.name): return
    group = frappe.get_doc("Consolidation Group", group_name, for_update=True)
    line = add_consolidation_line(group.name, "Warehouse Request", request.name)
    supply_bin = frappe.db.get_value("Production Supply Area", group.production_supply_area, "supply_bin")
    frappe.db.set_value("Consolidation Group Line", line, "final_destination_bin", supply_bin)
    open_requests = frappe.db.count("Warehouse Request", {"reference_doctype": "Material Request", "reference_name": request.reference_name,
        "status": ["not in", ["Completed", "Cancelled"]]})
    if open_requests: return  # the order is not complete yet: what has arrived waits in its location
    mr = frappe.get_doc("Material Request", request.reference_name)
    try:
        # A fresh pallet/carton for the finished set; its type falls back to WMS Settings' default.
        set_consolidation_target_hu(group.name, frappe.generate_hash(length=10))
        gather_consolidation_group(group.name)
    except frappe.ValidationError as e:
        frappe.clear_messages()
        mr.add_comment("Comment", _("Complete, but the delivery to the production area could not be started: {0}. Gather the Consolidation Group {1} manually.").format(e, group.name))


def on_group_gathered(group):
    """The set is on its HU in the supply bin: delivered. Post the ERPNext transfer and close the group."""
    if not group.material_request: return
    from frappe_wms.services.erp_sync_queue import dispatch
    for request_name in {l.reference_name for l in group.lines if l.reference_doctype == "Warehouse Request" and l.status != "Cancelled"}:
        dispatch("material_request_transfer", frappe.get_doc("Warehouse Request", request_name))
