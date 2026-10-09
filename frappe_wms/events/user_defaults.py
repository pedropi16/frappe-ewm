"""SU01-style WMS parameters on the User -> Frappe's per-user defaults.

Link fields to WMS Warehouse / WMS Stock Type / WMS Stock Owner pre-fill from these natively;
the Monitor and the stock adjustment form read the other two keys in JS.
"""
import frappe

# User field -> default key (a doctype name makes every Link to it pre-fill)
DEFAULTS = {
    "wms_default_warehouse": "WMS Warehouse",
    "wms_default_stock_type": "WMS Stock Type",
    "wms_default_stock_owner": "WMS Stock Owner",
    "wms_default_monitor_view": "wms_monitor_view",
    "wms_default_adjustment_type": "wms_adjustment_type",
}


def sync(doc, method=None):
    for field, key in DEFAULTS.items():
        value = doc.get(field)
        if value:
            frappe.defaults.set_user_default(key, value, doc.name)
        else:
            frappe.defaults.clear_default(key, parent=doc.name)
