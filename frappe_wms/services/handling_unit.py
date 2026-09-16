import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.numbering import find_number_range
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

HU_ROLES = ("WMS Operator", "WMS Receiver", "WMS Picker", "WMS Packer", "WMS Supervisor")

def _log_event(hu, event_type, **kwargs):
    frappe.get_doc({
        "doctype": "Handling Unit Event", "handling_unit": hu, "event_type": event_type,
        "event_timestamp": now_datetime(), "performed_by": frappe.session.user, **kwargs,
    }).insert(ignore_permissions=True)

def list_handling_units(search=None, warehouse=None, storage_bin=None):
    require_role(*HU_ROLES)
    resource = my_resource()
    filters = {}
    if warehouse or (resource and resource.warehouse): filters["warehouse"] = warehouse or resource.warehouse
    if storage_bin: filters["current_bin"] = storage_bin
    if search: filters["hu_number"] = ["like", f"%{search}%"]
    return frappe.get_list("Handling Unit", filters=filters,
        fields=["name", "hu_number", "hu_type", "warehouse", "current_bin", "parent_hu", "status", "stock_status"],
        order_by="modified desc", limit=50)

def create_handling_unit(hu_number, hu_type, storage_bin=None, parent_hu=None, warehouse=None):
    # Numbering, warehouse derivation, bin-vs-parent resolution and the "Created" HU Event all
    # live in the Handling Unit controller (before_insert/after_insert) so every creation path -
    # this API, Goods Receipt auto-registration, and a plain Desk "New Handling Unit" - behaves
    # identically instead of re-implementing the same rules three times.
    require_role(*HU_ROLES)
    resource = my_resource()
    warehouse = warehouse or (resource.warehouse if resource else None)
    hu = frappe.get_doc({
        "doctype": "Handling Unit", "hu_number": hu_number, "hu_type": hu_type,
        "warehouse": warehouse, "current_bin": storage_bin, "parent_hu": parent_hu,
    })
    hu.flags.wms_service_update = True
    hu.insert(ignore_permissions=True)
    return hu.as_dict()

def get_or_create_handling_unit(hu_number, hu_type=None, storage_bin=None, warehouse=None):
    # Shared by any flow that lets an operator scan a barcode that may or may not already be a
    # registered Handling Unit (Goods Receipt lines, RF "unknown barcode" auto-registration).
    # Falls back to WMS Settings.default_handling_unit_type so an unconfigured hu_type doesn't
    # dead-end receiving, matching the RF "Receive" behavior documented in the README.
    if frappe.db.exists("Handling Unit", hu_number):
        return hu_number
    hu_type = hu_type or frappe.db.get_single_value("WMS Settings", "default_handling_unit_type")
    if not hu_type: frappe.throw(_("Handling Unit {0} does not exist; specify a Handling Unit Type to create it").format(hu_number))
    if frappe.db.get_value("Handling Unit Type", hu_type, "numbering_mode") == "Internal":
        frappe.throw(_("HU Type {0} is internally numbered; scan an existing Handling Unit instead").format(hu_type))
    doc = frappe.get_doc({
        "doctype": "Handling Unit", "hu_number": hu_number, "hu_type": hu_type,
        "warehouse": warehouse, "current_bin": storage_bin,
    })
    doc.flags.wms_service_update = True
    doc.insert(ignore_permissions=True)
    return doc.name

def recycle_handling_unit(hu_name):
    # SAP EWM: an empty, reusable HU can be taken out of service and its number returned to the
    # pool so the next HU created against that Number Range gets it back, instead of numbers
    # only ever climbing forward.
    require_role(*HU_ROLES)
    hu = frappe.get_doc("Handling Unit", hu_name)
    if hu.stock_status != "Empty": frappe.throw(_("Only an empty Handling Unit can be recycled"))
    if hu.parent_hu: frappe.throw(_("Unnest {0} before recycling it").format(hu_name))
    if frappe.db.exists("Handling Unit", {"parent_hu": hu_name}):
        frappe.throw(_("{0} still has nested Handling Units; unnest them first").format(hu_name))
    hu_type = frappe.get_cached_doc("Handling Unit Type", hu.hu_type)
    if not hu_type.reusable: frappe.throw(_("HU Type {0} is not marked Reusable").format(hu.hu_type))
    range_name = None
    if hu_type.numbering_mode == "Internal":
        range_name = find_number_range("Handling Unit", warehouse=hu.warehouse, hu_type=hu.hu_type)
    _log_event(hu.name, "Cancelled", status_before=hu.status, bin_before=hu.current_bin)
    # Delete first, free the number only once that succeeds - otherwise a failed delete would
    # leave the number in the pool while the original document still physically exists.
    frappe.delete_doc("Handling Unit", hu.name, ignore_permissions=True, force=True)
    if range_name:
        frappe.get_doc({"doctype": "WMS HU Number Pool", "number_range": range_name, "hu_number": hu.hu_number}).insert(ignore_permissions=True)
    return {"handling_unit": hu_name, "recycled": True}

def recompute_measurements(hu_name):
    # The single choke point every stock posting against an HU passes through
    # (services/stock.post_entries calls this) - keeps gross/net weight, volume and stock_status
    # live from WMS Product.gross_weight_per_unit/volume_per_unit instead of the dead fields
    # they started as. Storage Bin.current_weight (tasks.recalculate_stale_bin_capacity) and
    # capacity-aware putaway (services/determination.py) both depend on this being accurate.
    hu = frappe.db.get_value("Handling Unit", hu_name, ["tare_weight", "hu_type"], as_dict=True)
    if not hu: return
    rows = frappe.db.sql("""
        select b.quantity, p.gross_weight_per_unit, p.volume_per_unit
        from `tabWMS Stock Balance` b left join `tabWMS Product` p on p.item = b.product
        where b.handling_unit=%s and b.quantity > 0
    """, hu_name, as_dict=True)
    total_qty = sum(flt(r.quantity) for r in rows)
    net_weight = sum(flt(r.quantity) * flt(r.gross_weight_per_unit) for r in rows)
    volume = sum(flt(r.quantity) * flt(r.volume_per_unit) for r in rows)
    gross_weight = net_weight + flt(hu.tare_weight)
    limits = frappe.db.get_value("Handling Unit Type", hu.hu_type, ["maximum_weight", "maximum_volume"], as_dict=True) if hu.hu_type else None
    if total_qty <= 0:
        stock_status = "Empty"
    elif limits and ((limits.maximum_weight and gross_weight >= limits.maximum_weight) or (limits.maximum_volume and volume >= limits.maximum_volume)):
        stock_status = "Full"
    else:
        stock_status = "Partial"
    frappe.db.set_value("Handling Unit", hu_name,
        {"net_weight": net_weight, "gross_weight": gross_weight, "volume": volume, "stock_status": stock_status},
        update_modified=False)

def nest_handling_unit(hu_name, parent_hu):
    require_role(*HU_ROLES)
    if hu_name == parent_hu: frappe.throw(_("A Handling Unit cannot nest inside itself"))
    hu = frappe.get_doc("Handling Unit", hu_name)
    if hu.parent_hu: frappe.throw(_("{0} is already nested inside {1}; unnest it first").format(hu_name, hu.parent_hu))
    parent = frappe.get_doc("Handling Unit", parent_hu)
    if parent.warehouse != hu.warehouse: frappe.throw(_("Parent and child Handling Units must be in the same warehouse"))
    before = {"parent_hu_before": hu.parent_hu, "bin_before": hu.current_bin}
    hu.parent_hu = parent_hu
    hu.current_bin = parent.current_bin
    hu.flags.wms_service_update = True
    hu.save(ignore_permissions=True)
    _log_event(hu.name, "Nested", parent_hu_after=parent_hu, bin_after=hu.current_bin, **before)
    return hu.as_dict()

def unnest_handling_unit(hu_name):
    require_role(*HU_ROLES)
    hu = frappe.get_doc("Handling Unit", hu_name)
    if not hu.parent_hu: frappe.throw(_("{0} is not nested inside another Handling Unit").format(hu_name))
    before = {"parent_hu_before": hu.parent_hu, "bin_before": hu.current_bin}
    hu.parent_hu = None
    hu.flags.wms_service_update = True
    hu.save(ignore_permissions=True)
    _log_event(hu.name, "Unnested", parent_hu_after=None, bin_after=hu.current_bin, **before)
    return hu.as_dict()

def set_handling_unit_blocked(hu_name, blocked, reason_code=None, remarks=None):
    require_role(*HU_ROLES)
    hu = frappe.get_doc("Handling Unit", hu_name)
    if blocked and hu.status == "Blocked": frappe.throw(_("{0} is already blocked").format(hu_name))
    if not blocked and hu.status != "Blocked": frappe.throw(_("{0} is not blocked").format(hu_name))
    status_before = hu.status
    hu.db_set("status", "Blocked" if blocked else "Open", update_modified=True)
    _log_event(hu.name, "Blocked" if blocked else "Unblocked", status_before=status_before, status_after=hu.status, reason_code=reason_code, remarks=remarks)
    return {"handling_unit": hu.name, "status": hu.status}

def handling_unit_detail(hu_name):
    require_role(*HU_ROLES)
    hu = frappe.get_doc("Handling Unit", hu_name)
    stock = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu.name, "quantity": [">", 0]},
        fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom", "storage_bin"])
    children = frappe.get_all("Handling Unit", filters={"parent_hu": hu.name}, fields=["name", "hu_number", "status", "stock_status"])
    events = frappe.get_all("Handling Unit Event", filters={"handling_unit": hu.name},
        fields=["event_type", "event_timestamp", "performed_by", "bin_before", "bin_after", "parent_hu_before", "parent_hu_after"],
        order_by="event_timestamp desc", limit=20)
    doc = hu.as_dict()
    doc["stock"] = stock
    doc["children"] = children
    doc["events"] = events
    return doc
