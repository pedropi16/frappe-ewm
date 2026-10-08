import frappe


def execute():
    for code, name, category, effect in (("511", "Unplanned Receipt", "Unplanned Receipt", "Increase"), ("551", "Scrapping", "Scrapping", "Decrease")):
        if not frappe.db.exists("WMS Movement Type", code):
            frappe.get_doc({"doctype": "WMS Movement Type", "movement_type_code": code, "movement_type_name": name, "movement_category": category, "inventory_effect": effect, "active": 1}).insert(ignore_permissions=True)
