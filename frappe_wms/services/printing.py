import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.utils import require_role

PRINT_ROLES = ("WMS Operator", "WMS Supervisor", "WMS Process Engineer")

def _matching_rule(warehouse, event):
    # Same "priority ascending, first full match wins" pattern as Process/Bin Determination
    # Rule (services/determination.py) - kept here rather than folded into that module since
    # print determination only ever matches on warehouse + event, not the wider item/storage
    # dimensions the other rule tables support.
    return frappe.db.get_value("WMS Print Determination Rule",
        {"active": 1, "warehouse": warehouse, "event": event}, ["name", "output_device", "print_format"],
        order_by="priority asc", as_dict=True)

def create_print_spool(reference_doctype, reference_name, event, warehouse):
    # Printing is opt-in, exactly like WMS Number Range: no matching Print Determination Rule
    # for this warehouse+event means nothing gets queued, not an error - configure a rule only
    # where you actually want this event to print something.
    rule = _matching_rule(warehouse, event)
    if not rule: return None
    spool = frappe.get_doc({
        "doctype": "WMS Print Spool", "warehouse": warehouse, "event": event,
        "reference_doctype": reference_doctype, "reference_name": reference_name,
        "output_device": rule.output_device, "print_format": rule.print_format,
        "status": "Queued", "requested_at": now_datetime(), "requested_by": frappe.session.user,
    })
    spool.insert(ignore_permissions=True)
    return spool.name

def list_queued_spools(warehouse=None, output_device=None):
    require_role(*PRINT_ROLES)
    filters = {"status": "Queued"}
    if warehouse: filters["warehouse"] = warehouse
    if output_device: filters["output_device"] = output_device
    return frappe.get_list("WMS Print Spool", filters=filters,
        fields=["name", "warehouse", "event", "reference_doctype", "reference_name",
            "output_device", "print_format", "requested_at", "requested_by"],
        order_by="requested_at asc", limit=100)

def mark_printed(spool_name):
    require_role(*PRINT_ROLES)
    spool = frappe.get_doc("WMS Print Spool", spool_name)
    if spool.status != "Queued": frappe.throw(_("Only a Queued spool entry can be marked Printed"))
    spool.db_set({"status": "Printed", "printed_at": now_datetime(), "printed_by": frappe.session.user}, update_modified=True)
    return {"spool": spool.name, "status": "Printed"}

def mark_failed(spool_name, reason=None):
    require_role(*PRINT_ROLES)
    spool = frappe.get_doc("WMS Print Spool", spool_name)
    if spool.status != "Queued": frappe.throw(_("Only a Queued spool entry can be marked Failed"))
    spool.db_set({"status": "Failed", "remarks": reason}, update_modified=True)
    return {"spool": spool.name, "status": "Failed"}
