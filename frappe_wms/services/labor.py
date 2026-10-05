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
