"""Engineered labor standards: planned seconds = (base allowance + seconds per unit x quantity + travel + handling) x (1 + PF&D %)."""
from frappe.utils import flt

from frappe_wms.services.travel import bin_distance
import frappe


def planned_task_seconds(standard, task):
    quantity = flt(task.planned_quantity)
    per_unit = frappe.db.get_value("WMS Product", {"item": task.product}, ["gross_weight_per_unit", "volume_per_unit"], as_dict=True) if task.product else None
    weight = flt(per_unit.gross_weight_per_unit) * quantity if per_unit else 0
    volume = flt(per_unit.volume_per_unit) * quantity if per_unit else 0
    seconds = (flt(standard.get("base_allowance_seconds")) + flt(standard.standard_seconds_per_unit) * quantity
        + flt(standard.get("travel_seconds_per_meter")) * flt(bin_distance(task.get("source_bin"), task.get("destination_bin")))
        + flt(standard.get("handling_seconds_per_kg")) * weight + flt(standard.get("handling_seconds_per_volume")) * volume)
    return seconds * (1 + flt(standard.get("pfd_percent")) / 100)


# ------------------------------------------------------------------ shifts and indirect labor

def start_indirect(resource, activity):
    """A worker leaves task work for indirect labor (break, cleaning, training...). Any open entry is closed first."""
    from frappe.utils import now_datetime
    from frappe_wms.utils import require_role
    require_role("WMS Operator", "WMS Picker", "WMS Packer", "WMS Receiver", "WMS Loader", "WMS Supervisor", "WMS Yard Clerk")
    stop_indirect(resource)
    warehouse = frappe.db.get_value("WMS Resource", resource, "warehouse")
    return frappe.get_doc({"doctype": "Indirect Labor Entry", "warehouse": warehouse, "resource": resource, "activity": activity,
                           "started_at": now_datetime()}).insert(ignore_permissions=True).name


def stop_indirect(resource):
    from frappe.utils import now_datetime, time_diff_in_seconds
    now, closed = now_datetime(), []
    for e in frappe.get_all("Indirect Labor Entry", filters={"resource": resource, "ended_at": ["is", "not set"]}, fields=["name", "started_at"]):
        frappe.db.set_value("Indirect Labor Entry", e.name, {"ended_at": now, "minutes": round(time_diff_in_seconds(now, e.started_at) / 60, 2)})
        closed.append(e.name)
    return closed


def shift_hours(warehouse):
    """Length of the warehouse's longest active shift (ponytail: one shift length per warehouse; per-shift attendance when that is needed)."""
    from frappe.utils import get_time
    hours = [((get_time(s.end_time).hour * 60 + get_time(s.end_time).minute) - (get_time(s.start_time).hour * 60 + get_time(s.start_time).minute)) % (24 * 60) / 60
             for s in frappe.get_all("Labor Shift", filters={"warehouse": warehouse, "active": 1}, fields=["start_time", "end_time"])]
    return max(hours, default=0)


def labor_summary(warehouse, from_date, to_date):
    """Per resource: direct hours (confirmed task time), indirect hours by activity, and utilisation against the shift length on its working days."""
    rows = {}
    direct = frappe.get_all("Warehouse Task", filters={"warehouse": warehouse, "status": "Confirmed", "assigned_resource": ["is", "set"],
                            "confirmed_at": ["between", [from_date, f"{to_date} 23:59:59"]]}, fields=["assigned_resource", "started_at", "confirmed_at"])
    for t in direct:
        r = rows.setdefault(t.assigned_resource, {"resource": t.assigned_resource, "direct_hours": 0.0, "indirect_hours": {}, "days": set()})
        if t.started_at and t.confirmed_at: r["direct_hours"] += (t.confirmed_at - t.started_at).total_seconds() / 3600
        r["days"].add(t.confirmed_at.date())
    for e in frappe.get_all("Indirect Labor Entry", filters={"warehouse": warehouse, "ended_at": ["is", "set"], "started_at": ["between", [from_date, f"{to_date} 23:59:59"]]},
                            fields=["resource", "activity", "minutes", "started_at"]):
        r = rows.setdefault(e.resource, {"resource": e.resource, "direct_hours": 0.0, "indirect_hours": {}, "days": set()})
        r["indirect_hours"][e.activity] = r["indirect_hours"].get(e.activity, 0) + flt(e.minutes) / 60
        r["days"].add(e.started_at.date())
    per_day = shift_hours(warehouse)
    out = []
    for r in rows.values():
        available = per_day * len(r["days"])
        busy = r["direct_hours"] + sum(r["indirect_hours"].values())
        out.append({"resource": r["resource"], "days": len(r["days"]), "direct_hours": round(r["direct_hours"], 2),
                    "indirect_hours": {k: round(v, 2) for k, v in r["indirect_hours"].items()},
                    "direct_percent": round(r["direct_hours"] / available * 100, 1) if available else None,
                    "utilization_percent": round(busy / available * 100, 1) if available else None})
    return out
