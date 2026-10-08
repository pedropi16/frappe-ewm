"""Dangerous goods (SAP EWM DG management, storage side): a product's hazard class decides which
storage types accept it, which classes may share a bin (segregation) and how many hazard points a
bin may carry. Enforced inside bin_rules.bin_violations when the warehouse switches it on, so
putaway, bin determination and manual moves all respect it."""
import frappe
from frappe import _
from frappe.utils import flt


def dg_info(item):
    return frappe.db.get_value("WMS Product", item, ["hazard_class", "dg_points_per_unit"], as_dict=True) or frappe._dict()


def _incompatible(a, b):
    return bool(frappe.db.exists("Hazard Class Link", {"parent": a, "parenttype": "Hazard Class", "hazard_class": b})
                or frappe.db.exists("Hazard Class Link", {"parent": b, "parenttype": "Hazard Class", "hazard_class": a}))


def dg_violations(bin_doc, storage_type, item, incoming_quantity):
    if not item or not frappe.db.get_value("WMS Warehouse", bin_doc.warehouse, "dangerous_goods_check"): return []
    info = dg_info(item)
    if not info.hazard_class: return []
    reasons = []
    allowed = {r.hazard_class for r in storage_type.get("allowed_hazard_classes") or []}
    if info.hazard_class not in allowed:
        reasons.append(_("hazard class {0} is not allowed in storage type {1}").format(info.hazard_class, storage_type.name))
    held = frappe.db.sql("""select b.quantity, p.hazard_class, p.dg_points_per_unit from `tabWMS Stock Balance` b join `tabWMS Product` p on p.name = b.product
        where b.storage_bin=%s and b.quantity>0""", bin_doc.name, as_dict=True)
    for other in sorted({h.hazard_class for h in held if h.hazard_class and h.hazard_class != info.hazard_class}):
        if _incompatible(info.hazard_class, other):
            reasons.append(_("hazard class {0} must not be stored with {1}").format(info.hazard_class, other))
    limit = flt(storage_type.get("dg_points_limit"))
    if limit and flt(info.dg_points_per_unit):
        points = sum(flt(h.quantity) * flt(h.dg_points_per_unit) for h in held if h.hazard_class) + flt(incoming_quantity) * flt(info.dg_points_per_unit)
        if points > limit + 1e-6:
            reasons.append(_("hazard points {0} would exceed the limit {1}").format(round(points, 3), limit))
    return reasons
