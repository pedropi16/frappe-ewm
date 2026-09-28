import frappe

def execute():
    # after_install only ever runs once, at first install - an already-installed site (including
    # production) never gets a movement type added to install.py's list afterwards. Same shape
    # as the blind_counting backfill above.
    if not frappe.db.exists("WMS Movement Type", "561"):
        frappe.get_doc({
            "doctype": "WMS Movement Type", "movement_type_code": "561", "movement_type_name": "Opening Stock Balance",
            "movement_category": "Receipt", "inventory_effect": "Increase", "active": 1,
        }).insert(ignore_permissions=True)
