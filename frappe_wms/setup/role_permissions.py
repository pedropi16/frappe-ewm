"""The WMS authorization matrix - which role may read or maintain which DocType.

Warehouse state (tasks, stock, HUs, deliveries...) changes only through the WMS services, which
check roles themselves (utils.require_role) and write with ignore_permissions; DocType
permissions govern what people can *see* and which configuration they can *maintain* in the
desk. The matrix, like SAP roles:

  every WMS role        reads the warehouse structure and all execution documents
  WMS Process Engineer  maintains the customizing (structure, rules, process setup, printers)
  WMS Master Data       maintains product and storage master data
  WMS Supervisor        keeps the write access it already had (waves, orders, deliveries...)

apply_to_doctype_json() writes the matrix into the app's DocType definitions (fresh installs);
ensure_role_permissions() runs after every migrate for sites whose permissions were customised
(Custom DocPerm rows make Frappe ignore the DocType's own), adding what is missing, never
removing anything an administrator granted.
"""
import frappe

ALL_WMS_ROLES = ["WMS Operator", "WMS Receiver", "WMS Picker", "WMS Packer", "WMS Loader", "WMS Inventory Controller",
                 "WMS Supervisor", "WMS Process Engineer", "WMS Master Data", "WMS Administrator", "WMS Integration User", "WMS Auditor"]
READ = {"read": 1, "report": 1, "export": 1, "print": 1}
MAINTAIN = {"read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1, "print": 1}

# Readable by every WMS role.
EXECUTION = [
    "Consolidation Group", "Kitting Order", "Warehouse Order", "Warehouse Queue", "Warehouse Request", "Warehouse Task",
    "WMS Exception Code", "WMS Print Spool", "WMS Resource", "WMS Resource Group", "WMS Task Difference",
    "Handling Unit", "Handling Unit Event", "Goods Receipt", "Inbound Delivery", "WMS Batch Characteristic Value",
    "WMS Physical Inventory Count", "WMS Posting Change", "WMS Quality Inspection", "WMS Stock Balance", "WMS Stock Ledger Entry",
    "Goods Issue", "Production Material Request", "Outbound Delivery", "Packing Order", "Stock Allocation", "VAS Order", "WMS Wave", "WMS Shipment",
    "WMS Dock Appointment", "WMS Transportation Unit", "WMS Yard Task",
]
# Customizing: readable by every WMS role, maintained by WMS Process Engineer.
CUSTOMIZING = [
    "Activity Area", "Bin Type", "Storage Bin", "Storage Section", "Storage Type", "WMS Warehouse", "Work Center",
    "Handling Unit Type", "Packaging Material", "Packaging Spec", "WMS Product", "WMS Product Warehouse", "WMS Stock Type",
    "Billing Rate", "Bin Determination Rule", "Count Tolerance Group", "Cycle Count Rule", "Inspection Rule", "Labor Standard",
    "Process Determination Rule", "Removal Rule", "Replenishment Rule", "Storage Process", "Storage Type Search Sequence",
    "Warehouse Process Type", "Warehouse Process Type Determination Rule", "Wave Template", "WMS Movement Type",
    "WO Creation Rule", "WMS Route", "WMS Print Determination Rule", "WMS Number Range", "WMS HU Number Pool",
    "Warehouse Queue", "WMS Exception Code", "WMS Resource", "WMS Resource Group", "Production Supply Area", "Storage Group", "Handling Indicator", "Layout Storage Control", "Door Determination Rule", "Means of Transport", "Hazard Class",
    "Putaway Control Indicator", "Stock Removal Control Indicator", "Storage Section Indicator", "Production Supply Control Cycle", "WMS Block Reason", "Handling Unit Type Group", "PPF Action Profile", "WMS Usage Decision", "WMS Stock Owner", "WMS Document Type", "WMS MFS Endpoint", "WMS MFS Telegram Type",
]
MASTER_DATA = ["Storage Bin", "Storage Section", "Activity Area", "Bin Type", "WMS Product", "WMS Product Warehouse",
               "Packaging Material", "Packaging Spec", "Handling Unit Type"]
# Settings and the posting queue are for the people running the system.
RESTRICTED = {
    "WMS Settings": {"WMS Process Engineer": MAINTAIN, "WMS Supervisor": READ, "WMS Auditor": READ},
    "WMS ERP Sync Log": {"WMS Supervisor": READ, "WMS Auditor": READ, "WMS Process Engineer": READ},
    "WMS Ledger Archive Run": {"WMS Supervisor": READ, "WMS Auditor": READ, "WMS Process Engineer": READ},
}


def matrix():
    """{doctype: {role: rights}} - rights to add on top of whatever a doctype already grants."""
    out = {}
    for dt in set(EXECUTION) | set(CUSTOMIZING):
        out.setdefault(dt, {}).update({r: dict(READ) for r in ALL_WMS_ROLES})
    for dt in CUSTOMIZING:
        out[dt]["WMS Process Engineer"] = dict(MAINTAIN)
    for dt in MASTER_DATA:
        out[dt]["WMS Master Data"] = dict(MAINTAIN)
    for dt, roles in RESTRICTED.items():
        out.setdefault(dt, {}).update({r: dict(v) for r, v in roles.items()})
    return out


def _merge(existing, rights):
    for k, v in rights.items():
        if v and not existing.get(k): existing[k] = v


def apply_to_doctype_json(app_path):
    """One-off/maintenance: writes the matrix into the DocType JSON files under app_path."""
    import glob
    import json
    wanted = matrix()
    changed = []
    for path in glob.glob(f"{app_path}/*/doctype/*/*.json"):
        raw = open(path).read()
        d = json.loads(raw)
        if d.get("doctype") != "DocType" or d["name"] not in wanted: continue
        perms = d.setdefault("permissions", [])
        before = json.dumps(perms)
        for role, rights in wanted[d["name"]].items():
            row = next((p for p in perms if p.get("role") == role and not p.get("permlevel")), None)
            if not row:
                row = {"role": role}
                perms.append(row)
            _merge(row, rights)
        if json.dumps(perms) != before:
            indent = 1 if raw.startswith('{\n "') else 2
            open(path, "w").write(json.dumps(d, indent=indent, ensure_ascii=False) + ("\n" if raw.endswith("\n") else ""))
            changed.append(d["name"])
    return changed


def ensure_role_permissions():
    """after_migrate: sites with Custom DocPerm rows for a doctype get the matrix added there too."""
    from frappe.permissions import add_permission, update_permission_property
    from frappe_wms.setup.roles import ensure_roles
    ensure_roles()
    for doctype, roles in matrix().items():
        if not frappe.db.exists("DocType", doctype) or not frappe.db.exists("Custom DocPerm", {"parent": doctype}): continue
        for role, rights in roles.items():
            if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
                add_permission(doctype, role, 0)
            row = frappe.db.get_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}, list(rights), as_dict=True)
            for right, value in rights.items():
                if value and not row.get(right):
                    update_permission_property(doctype, role, 0, right, value)
