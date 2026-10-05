"""Handling indicators (SAP's warehouse handling indicator): per-product handling rules that are configurable.

* Required Storage Group: the product only goes into bins of that storage group (bin_rules.bin_violations).
* Do Not Unpack: the product stays in its Handling Unit (task confirmation, repacking).
"""
import frappe
from frappe import _


def product_handling(item):
    """({required storage groups}, no_unpack) over the product's active indicators."""
    rows = frappe.db.sql("""select i.required_storage_group, i.no_unpack from `tabWMS Product Handling Indicator` r join `tabHandling Indicator` i on i.name = r.indicator
        join `tabWMS Product` p on p.name = r.parent where p.item=%s and i.active=1""", item, as_dict=True) if item else []
    return {r.required_storage_group for r in rows if r.required_storage_group}, any(r.no_unpack for r in rows)


def check_unpack(item, source_hu, destination_hu):
    """Refuse moving a no-unpack product out of its Handling Unit (into another HU, or loose)."""
    if source_hu and source_hu != destination_hu and product_handling(item)[1]:
        frappe.throw(_("{0} must not be unpacked: it stays in its Handling Unit {1}").format(item, source_hu))
