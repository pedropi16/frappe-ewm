import frappe
from frappe.utils import flt, add_days, nowdate

def _scope_recently_covered(warehouse, frequency_days, *, product=None, storage_bin=None):
    # A single check covers both "still open" and "already posted within the window" -
    # WMS Physical Inventory Count keeps its original count_date through every status, so
    # this one filter is enough to avoid regenerating a count for the same scope too often.
    return frappe.db.exists("WMS Physical Inventory Count", {
        "warehouse": warehouse, "count_date": [">=", add_days(nowdate(), -(frequency_days or 0))],
        "product": product if product else ["in", ["", None]],
        "storage_bin": storage_bin if storage_bin else ["in", ["", None]],
    })

def _create_count(warehouse, *, product=None, storage_bin=None):
    doc = frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": warehouse,
        "product": product, "storage_bin": storage_bin, "status": "Draft"})
    doc.insert(ignore_permissions=True)
    return doc.name

def _generate_abc_counts(rule):
    created = []
    if not rule.abc_indicator: return created
    items = frappe.get_all("WMS Product", filters={"abc_indicator": rule.abc_indicator, "warehouse_managed": 1, "active": 1}, pluck="item")
    for item in items:
        if _scope_recently_covered(rule.warehouse, rule.frequency_days, product=item): continue
        created.append(_create_count(rule.warehouse, product=item))
    return created

def _generate_low_stock_counts(rule):
    created = []
    for rr in frappe.get_all("Replenishment Rule", filters={"warehouse": rule.warehouse, "active": 1},
            fields=["product", "storage_bin", "stock_type", "minimum_quantity"]):
        current = flt(frappe.db.get_value("WMS Stock Balance",
            {"warehouse": rule.warehouse, "product": rr.product, "storage_bin": rr.storage_bin, "stock_type": rr.stock_type}, "quantity"))
        if current >= flt(rr.minimum_quantity): continue
        if _scope_recently_covered(rule.warehouse, rule.frequency_days, storage_bin=rr.storage_bin): continue
        created.append(_create_count(rule.warehouse, storage_bin=rr.storage_bin))
    return created

def _generate_zero_stock_counts(rule):
    # A WMS Stock Balance row only exists once a bin/product combo has had real activity -
    # a bin that has simply never been stocked has no balance row at all, so filtering on
    # quantity=0 here (rather than "never had stock") already excludes a fresh empty bin.
    created = []
    filters = {"warehouse": rule.warehouse, "quantity": 0}
    if rule.storage_type:
        filters["storage_bin"] = ["in", frappe.get_all("Storage Bin", filters={"warehouse": rule.warehouse, "storage_type": rule.storage_type}, pluck="name")]
    bins = {b.storage_bin for b in frappe.get_all("WMS Stock Balance", filters=filters, fields=["storage_bin"])}
    for storage_bin in bins:
        if not storage_bin: continue
        if _scope_recently_covered(rule.warehouse, rule.frequency_days, storage_bin=storage_bin): continue
        created.append(_create_count(rule.warehouse, storage_bin=storage_bin))
    return created

def _generate_putaway_pi_counts(rule):
    created = []
    since = add_days(nowdate(), -(rule.frequency_days or 0))
    filters = {"warehouse": rule.warehouse, "movement_type": "201", "posting_datetime": [">=", since]}
    bins = {b.storage_bin for b in frappe.get_all("WMS Stock Ledger Entry", filters=filters, fields=["storage_bin"])}
    if rule.storage_type:
        bins_in_type = set(frappe.get_all("Storage Bin", filters={"warehouse": rule.warehouse, "storage_type": rule.storage_type}, pluck="name"))
        bins &= bins_in_type
    for storage_bin in bins:
        if not storage_bin: continue
        if _scope_recently_covered(rule.warehouse, rule.frequency_days, storage_bin=storage_bin): continue
        created.append(_create_count(rule.warehouse, storage_bin=storage_bin))
    return created

def _generate_bin_check_counts(rule):
    # Bins are processed oldest-uncounted-first (no per-run cap in this pass) so a
    # supervisor reviewing generated counts sees the most overdue bins surfaced first.
    created = []
    if not rule.storage_type: return created
    bins = frappe.get_all("Storage Bin", filters={"warehouse": rule.warehouse, "storage_type": rule.storage_type, "active": 1}, pluck="name", order_by="sequence asc")
    for storage_bin in bins:
        if _scope_recently_covered(rule.warehouse, rule.frequency_days, storage_bin=storage_bin): continue
        created.append(_create_count(rule.warehouse, storage_bin=storage_bin))
    return created

def _generate_annual_count(rule):
    if _scope_recently_covered(rule.warehouse, rule.frequency_days): return []
    return [_create_count(rule.warehouse)]

_GENERATORS = {
    "ABC Cycle Count": _generate_abc_counts,
    "Low Stock": _generate_low_stock_counts,
    "Zero Stock": _generate_zero_stock_counts,
    "Putaway PI": _generate_putaway_pi_counts,
    "Bin Check": _generate_bin_check_counts,
    "Annual": _generate_annual_count,
}

def generate_scheduled_counts():
    created = []
    for rule in frappe.get_all("Cycle Count Rule", filters={"active": 1}, fields=["*"]):
        generator = _GENERATORS.get(rule.procedure_type)
        if generator: created += generator(rule)
    return created
