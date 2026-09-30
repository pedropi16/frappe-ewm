import frappe
from frappe.permissions import add_permission, update_permission_property

DOCTYPE = "Inbound Delivery"
# The same gap grant_outbound_delivery_wms_role_permissions closed for Outbound Delivery, on the
# inbound side: api/inbound.py's RECEIVING_ROLES authorizes WMS Operator/Receiver/Supervisor for
# receiving, but the doctype only ever granted WMS Operator read. A WMS Receiver without WMS
# Operator couldn't even open a delivery on the RF Receive screen (it loads it with
# frappe.client.get), and a WMS Supervisor couldn't open or submit one from the desk - reproduced
# in a simulated shift as a PermissionError on every Inbound Delivery submit.
ROLES = ("WMS Operator", "WMS Receiver", "WMS Supervisor")
PTYPES = ("read", "write", "create", "submit")

def execute():
    # Custom DocPerm rows, not just the JSON: once a site has any Custom DocPerm for a doctype,
    # Frappe ignores the JSON defaults there (see the outbound patch for the full story).
    if not frappe.db.exists("Custom DocPerm", {"parent": DOCTYPE}):
        frappe.clear_cache(doctype=DOCTYPE)
        return
    for role in ROLES:
        if not frappe.db.get_value("Custom DocPerm", {"parent": DOCTYPE, "role": role, "permlevel": 0}):
            add_permission(DOCTYPE, role, 0)
        for ptype in PTYPES:
            update_permission_property(DOCTYPE, role, 0, ptype, 1, validate=False)
    frappe.clear_cache(doctype=DOCTYPE)
