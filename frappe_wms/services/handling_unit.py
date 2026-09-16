import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.numbering import next_number
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

def _resolve_hu_number(hu_number, hu_type, warehouse):
    # Internal HU types are always system-numbered (SAP EWM: an internal HU number range) -
    # any number passed by the caller (e.g. a stale RF form value) is ignored. External HU
    # types carry a number a person or a label printer already assigned, so it's required.
    numbering_mode = frappe.db.get_value("Handling Unit Type", hu_type, "numbering_mode") or "External"
    if numbering_mode == "Internal":
        return next_number("Handling Unit", warehouse=warehouse, hu_type=hu_type)
    if not hu_number: frappe.throw(_("HU Type {0} uses external numbering; an HU Number is required").format(hu_type))
    return hu_number

def create_handling_unit(hu_number, hu_type, storage_bin=None, parent_hu=None, warehouse=None):
    require_role(*HU_ROLES)
    resource = my_resource()
    warehouse = warehouse or (resource.warehouse if resource else None)
    if not warehouse: frappe.throw(_("A warehouse is required to create a Handling Unit"))
    if not storage_bin and not parent_hu: frappe.throw(_("A storage bin or a parent Handling Unit is required"))
    if parent_hu:
        parent = frappe.get_doc("Handling Unit", parent_hu)
        storage_bin = storage_bin or parent.current_bin
        if parent.current_bin != storage_bin: frappe.throw(_("A nested Handling Unit must be created in its parent's bin"))
    hu_number = _resolve_hu_number(hu_number, hu_type, warehouse)
    if frappe.db.exists("Handling Unit", hu_number): frappe.throw(_("Handling Unit {0} already exists").format(hu_number))
    hu = frappe.get_doc({
        "doctype": "Handling Unit", "hu_number": hu_number, "hu_type": hu_type, "warehouse": warehouse,
        "current_bin": storage_bin, "parent_hu": parent_hu, "status": "Created", "stock_status": "Empty",
    })
    hu.flags.wms_service_update = True
    hu.insert(ignore_permissions=True)
    _log_event(hu.name, "Created", bin_after=storage_bin, parent_hu_after=parent_hu, status_after=hu.status)
    return hu.as_dict()

def get_or_create_handling_unit(hu_number, hu_type=None, storage_bin=None, warehouse=None):
    # Shared by any flow that lets an operator scan a barcode that may or may not already be a
    # registered Handling Unit (Goods Receipt lines, RF "unknown barcode" auto-registration).
    # Falls back to WMS Settings.default_handling_unit_type so an unconfigured hu_type doesn't
    # dead-end receiving, matching the RF "Receive" behavior documented in the README.
    if frappe.db.exists("Handling Unit", hu_number):
        return frappe.get_doc("Handling Unit", hu_number).name
    hu_type = hu_type or frappe.db.get_single_value("WMS Settings", "default_handling_unit_type")
    if not hu_type: frappe.throw(_("Handling Unit {0} does not exist; specify a Handling Unit Type to create it").format(hu_number))
    if frappe.db.get_value("Handling Unit Type", hu_type, "numbering_mode") == "Internal":
        frappe.throw(_("HU Type {0} is internally numbered; scan an existing Handling Unit instead").format(hu_type))
    doc = frappe.get_doc({
        "doctype": "Handling Unit", "hu_number": hu_number, "hu_type": hu_type, "warehouse": warehouse,
        "current_bin": storage_bin, "status": "Open", "stock_status": "Empty",
    })
    doc.flags.wms_service_update = True
    doc.insert(ignore_permissions=True)
    _log_event(doc.name, "Created", bin_after=storage_bin, status_after=doc.status)
    return doc.name

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
