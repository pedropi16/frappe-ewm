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


def _bin_load(bin_name, hu_field, per_unit_field):
    """What a bin holds, in weight or volume: a top-level HU counts with its own measured value
    (gross weight / volume) when it has one, otherwise by its contents; loose stock counts as
    quantity x the product's per-unit value."""
    hus = {h.name: h for h in frappe.get_all("Handling Unit", filters={"current_bin": bin_name, "status": ["not in", ["Shipped", "Cancelled"]]},
                                             fields=["name", "parent_hu", hu_field])}

    def root(name):
        seen = set()
        while name in hus and hus[name].parent_hu and name not in seen:
            seen.add(name)
            name = hus[name].parent_hu
        return name
    measured = {n for n, h in hus.items() if not h.parent_hu and flt(h.get(hu_field)) > 0}
    total = sum(flt(hus[n].get(hu_field)) for n in measured)
    per_unit = {}
    for b in frappe.get_all("WMS Stock Balance", filters={"storage_bin": bin_name, "quantity": [">", 0]}, fields=["product", "quantity", "handling_unit"]):
        if b.handling_unit and root(b.handling_unit) in measured: continue
        if b.product not in per_unit:
            per_unit[b.product] = flt(frappe.db.get_value("WMS Product", b.product, per_unit_field))
        total += flt(b.quantity) * per_unit[b.product]
    return total


def live_weight(bin_name):
    return _bin_load(bin_name, "gross_weight", "gross_weight_per_unit")


def live_volume(bin_name):
    return _bin_load(bin_name, "volume", "volume_per_unit")


def incoming_load(product, quantity):
    """(weight, volume) of quantity units of product - None where the product has no value."""
    values = frappe.db.get_value("WMS Product", product, ["gross_weight_per_unit", "volume_per_unit"], as_dict=True) if product else None
    if not values: return None, None
    weight = flt(values.gross_weight_per_unit) * flt(quantity) if values.gross_weight_per_unit else None
    volume = flt(values.volume_per_unit) * flt(quantity) if values.volume_per_unit else None
    return weight, volume


def hu_load(hu_name):
    """(weight, volume) an HU brings to a bin: its measured gross weight / volume, otherwise its
    contents (nested HUs included)."""
    hu = frappe.db.get_value("Handling Unit", hu_name, ["gross_weight", "volume"], as_dict=True) or {}
    names, level = [hu_name], [hu_name]
    while level:
        level = frappe.get_all("Handling Unit", filters={"parent_hu": ["in", level]}, pluck="name")
        names += level
    weight, volume = flt(hu.get("gross_weight")), flt(hu.get("volume"))
    if not (weight and volume):
        contents_w = contents_v = 0
        for b in frappe.get_all("WMS Stock Balance", filters={"handling_unit": ["in", names], "quantity": [">", 0]}, fields=["product", "quantity"]):
            w, v = incoming_load(b.product, b.quantity)
            contents_w += flt(w); contents_v += flt(v)
        weight, volume = weight or contents_w, volume or contents_v
    return weight or None, volume or None


def bin_violations(bin_name, *, item=None, stock_type=None, hu_type=None, batch_no=None,
                    destination_hu=None, incoming_weight=None, incoming_hu_count=1, incoming_volume=None):
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
    elif method == "Volume" and bin_doc.maximum_volume and incoming_volume:
        if live_volume(bin_name) + flt(incoming_volume) > flt(bin_doc.maximum_volume):
            reasons.append(_("volume capacity exceeded"))

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
