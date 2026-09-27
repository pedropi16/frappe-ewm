import frappe
from frappe import _
from frappe.utils import flt


def _mixing_rules_enforced():
    return bool(frappe.get_cached_value("WMS Settings", "WMS Settings", "enforce_storage_type_rules"))


def live_hu_count(bin_name):
    # Storage Bin.current_hu_count is a cached counter only refreshed hourly
    # (tasks.recalculate_stale_bin_capacity) - two putaways landing in the same "first empty
    # bin" within that hour both passed the capacity check against the same stale number
    # (reproduced by reading the code: nothing here ever queried Handling Unit directly). Counts
    # only TOP-LEVEL HUs: a nested HU shares its parent's current_bin but isn't a second pallet
    # taking up a second slot - the cached counter used to charge it as one anyway.
    return frappe.db.count("Handling Unit", {
        "current_bin": bin_name, "parent_hu": ["in", ["", None]], "status": ["not in", ["Shipped", "Cancelled"]],
    })


def live_weight(bin_name):
    return flt(frappe.db.sql(
        "select coalesce(sum(gross_weight),0) from `tabHandling Unit` "
        "where current_bin=%s and (parent_hu is null or parent_hu='') and status not in ('Shipped','Cancelled')",
        bin_name,
    )[0][0])


def bin_violations(bin_name, *, item=None, stock_type=None, hu_type=None, batch_no=None,
                    destination_hu=None, incoming_weight=None, incoming_hu_count=1):
    bin_doc = frappe.get_doc("Storage Bin", bin_name)
    storage_type = frappe.get_cached_doc("Storage Type", bin_doc.storage_type)
    if not bin_doc.active or bin_doc.putaway_blocked:
        return [_("bin is inactive or blocked for putaway")]

    reasons = []
    if stock_type and bin_doc.allowed_stock_types:
        if stock_type not in {r.stock_type for r in bin_doc.allowed_stock_types}:
            reasons.append(_("stock type {0} is not in the bin's whitelist").format(stock_type))
    if hu_type and bin_doc.allowed_hu_types:
        if hu_type not in {r.hu_type for r in bin_doc.allowed_hu_types}:
            reasons.append(_("HU type {0} is not in the bin's whitelist").format(hu_type))

    method = storage_type.capacity_check_method
    if method == "HU Count" and bin_doc.maximum_hus:
        if live_hu_count(bin_name) + flt(incoming_hu_count) > flt(bin_doc.maximum_hus):
            reasons.append(_("HU count capacity exceeded"))
    elif method == "Weight" and bin_doc.maximum_weight and incoming_weight:
        if live_weight(bin_name) + flt(incoming_weight) > flt(bin_doc.maximum_weight):
            reasons.append(_("weight capacity exceeded"))
    # method == "Volume": Storage Bin has no current_volume tracking field yet, so this
    # cannot be checked - deliberately left unenforced rather than silently faked.

    if storage_type.hu_managed and not destination_hu:
        reasons.append(_("storage type requires a Handling Unit"))

    if _mixing_rules_enforced():
        occupants = frappe.get_all("WMS Stock Balance", filters={"storage_bin": bin_name, "quantity": [">", 0]},
            fields=["product", "stock_type", "batch_no"])
        if item and not storage_type.allow_mixed_products and any(o.product != item for o in occupants):
            reasons.append(_("bin already holds a different product"))
        if stock_type and not storage_type.allow_mixed_stock_types and any(o.stock_type != stock_type for o in occupants):
            reasons.append(_("bin already holds a different stock type"))
        if batch_no and not storage_type.allow_mixed_batches and any(o.batch_no and o.batch_no != batch_no for o in occupants):
            reasons.append(_("bin already holds a different batch"))
    return reasons


def validate_destination_bin(bin_name, **kwargs):
    reasons = bin_violations(bin_name, **kwargs)
    if reasons:
        frappe.throw(_("Storage Bin {0} cannot be used as a destination: {1}").format(bin_name, "; ".join(reasons)))
