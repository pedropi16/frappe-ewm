"""Cartonization (SAP EWM): plans which shipping HUs a delivery needs. Items are packed first-fit
decreasing into the packaging types flagged "Use for Cartonization", limited by their maximum
weight and volume (weight/volume per unit come from the WMS Product). The result is the
delivery's Planned Shipping HUs; the packing station proposes them and links the real HU."""
import math

import frappe
from frappe import _
from frappe.utils import flt

from frappe_wms.utils import require_role

EPS = 1e-9


def _fit(unit_w, unit_v, room_w, room_v):
    """Whole units that fit in the remaining room (None limit = unlimited)."""
    fits = [room / unit for room, unit in ((room_w, unit_w), (room_v, unit_v)) if room is not None and unit > EPS]
    return math.floor(min(fits) + EPS) if fits else 10 ** 9


def plan_cartons(delivery_name):
    require_role("WMS Supervisor", "WMS Administrator", "WMS Packer")
    d = frappe.get_doc("Outbound Delivery", delivery_name, for_update=True)
    if d.docstatus != 1 or d.closed_short or d.status in ("Completed", "Cancelled", "Goods Issued"):
        frappe.throw(_("Only an open, released delivery can be cartonized"))
    if any(flt(i.packed_quantity) > EPS for i in d.items) or frappe.db.exists("Handling Unit", {"outbound_delivery": d.name}):
        frappe.throw(_("{0} is already being packed; cartonization is only possible before").format(d.name))
    types = frappe.get_all("Handling Unit Type", {"cartonizable": 1, "active": 1}, ["name", "maximum_weight", "maximum_volume"], order_by="maximum_volume asc, maximum_weight asc")
    if not types: frappe.throw(_("No packaging is flagged \"Use for Cartonization\" on its Handling Unit Type"))

    # ponytail: weight/volume only (no 3D fit), whole units; add product dimensions when volume is too coarse
    cartons = []  # {type, room_w, room_v, rows: {item: [qty, w, v]}}
    lines = []
    for i in d.items:
        w, v = frappe.db.get_value("WMS Product", i.item, ["gross_weight_per_unit", "volume_per_unit"]) or (0, 0)
        if flt(w) <= EPS and flt(v) <= EPS:
            frappe.throw(_("Line {0}: {1} has neither weight nor volume per unit on its WMS Product").format(i.line_number, i.item))
        lines.append([i.item, flt(i.requested_quantity), flt(w), flt(v)])
    for item, qty, w, v in sorted(lines, key=lambda l: (l[3], l[2]), reverse=True):
        while qty > EPS:
            for c in cartons:
                fit = _fit(w, v, c["room_w"], c["room_v"])
                if fit >= 1: break
            else:
                c = _open_carton(types, item, qty, w, v)
                cartons.append(c)
                fit = _fit(w, v, c["room_w"], c["room_v"])
            k = min(qty, fit)
            row = c["rows"].setdefault(item, [0, 0, 0])
            row[0] += k; row[1] += k * w; row[2] += k * v
            if c["room_w"] is not None: c["room_w"] -= k * w
            if c["room_v"] is not None: c["room_v"] -= k * v
            qty -= k
    d.set("planned_hus", [])
    for n, c in enumerate(cartons, 1):
        for item, (q, w, v) in c["rows"].items():
            d.append("planned_hus", {"carton_no": n, "hu_type": c["type"], "item": item, "quantity": q, "weight": w, "volume": v})
    d.flags.ignore_permissions = True
    d.save()
    return {"cartons": len(cartons), "types": sorted({c["type"] for c in cartons})}


def _open_carton(types, item, qty, w, v):
    """Smallest packaging holding what is left of the line, else the largest one that holds a unit at all."""
    usable = [t for t in types if _fit(w, v, t.maximum_weight or None, t.maximum_volume or None) >= 1]
    if not usable:
        frappe.throw(_("{0} does not fit into any packaging flagged for cartonization").format(item))
    whole = [t for t in usable if _fit(w, v, t.maximum_weight or None, t.maximum_volume or None) >= qty]
    t = whole[0] if whole else usable[-1]
    return {"type": t.name, "room_w": t.maximum_weight or None, "room_v": t.maximum_volume or None, "rows": {}}


def next_planned_hu(delivery_name):
    """First planned carton without a real HU yet: (carton_no, hu_type) or None."""
    for r in frappe.get_all("Planned Shipping HU", {"parent": delivery_name}, ["carton_no", "hu_type", "handling_unit"], order_by="carton_no asc"):
        if not r.handling_unit: return r.carton_no, r.hu_type


def link_planned_hu(delivery_name, carton_no, hu_name):
    frappe.db.set_value("Planned Shipping HU", {"parent": delivery_name, "carton_no": carton_no}, "handling_unit", hu_name)
