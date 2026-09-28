import frappe
from frappe.permissions import add_permission, update_permission_property

DOCTYPE = "Outbound Delivery"
# Exactly the roles api/outbound.py's own OUTBOUND_ROLES and services/allocation.py::
# allocate_delivery's require_role() calls already declare authorized for outbound work - the
# doctype-level permission rows were simply never added to match, so every one of them 403'd in
# production (confirmed live) the first time a real WMS-role-only user (nobody also holding
# System Manager/WMS Administrator) tried to progress an Outbound Delivery: allocate_delivery's
# own doc.check_permission("write") has nothing to grant it.
ROLES = ("WMS Operator", "WMS Picker", "WMS Loader", "WMS Supervisor")
PTYPES = ("read", "write", "create", "submit")

def execute():
    # frappe_wms ships zero permission fixtures anywhere in the repo - every role permission on
    # this doctype has always been 100% manual, site-level Role Permission Manager configuration
    # (confirmed on production: Custom DocPerm rows already exist for it, meaning some earlier
    # manual edit already shadowed outbound_delivery.json's own `permissions` block for that site
    # - once any Custom DocPerm row exists for a doctype, Frappe permanently ignores its JSON
    # defaults there). Editing the JSON alone would never reach an already-customized site, so
    # this patch writes the Custom DocPerm rows directly instead - it reaches every site the same
    # way regardless of whether Role Permission Manager was ever opened on it before.
    for role in ROLES:
        if not frappe.db.get_value("Custom DocPerm", {"parent": DOCTYPE, "role": role, "permlevel": 0}):
            add_permission(DOCTYPE, role, 0)
        for ptype in PTYPES:
            update_permission_property(DOCTYPE, role, 0, ptype, 1, validate=False)
    frappe.clear_cache(doctype=DOCTYPE)
