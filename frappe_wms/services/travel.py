"""Bin coordinates, bin sorting per activity and travel distance (SAP: bin sorting per activity area, travel distance calculation).

A bin has a position (x, y, z). Distance is Manhattan (|dx| + |dy| + |dz|); a bin whose coordinates are all zero counts as having none,
and a leg with an unknown end has no distance. Bins of an Activity Area get a walk-path order per activity (Activity Area > Bin Sort);
generate_walk_path fills it from the coordinates as a serpentine through the aisles.
"""
import frappe
from frappe import _
from frappe.utils import flt


def _position(bin_name):
    if not bin_name: return None
    x, y, z = (flt(v) for v in (frappe.db.get_value("Storage Bin", bin_name, ["x_coordinate", "y_coordinate", "z_coordinate"]) or (0, 0, 0)))
    return None if not (x or y or z) else (x, y, z)


def bin_distance(a, b):
    pa, pb = _position(a), _position(b)
    return None if pa is None or pb is None else sum(abs(p - q) for p, q in zip(pa, pb))


def path_distance(bins):
    """Sum of the known legs along bins in the order given."""
    bins = [b for i, b in enumerate(bins) if b and (i == 0 or b != bins[i - 1])]
    return sum(d for d in (bin_distance(a, b) for a, b in zip(bins, bins[1:])) if d)


def sort_sequence(bin_name, activity):
    """The bin's place in the walk path of its activity area for an activity; without an entry, its own sequence."""
    area = frappe.db.get_value("Storage Bin", bin_name, ["activity_area", "sequence"], as_dict=True) if bin_name else None
    if not area: return 0
    if area.activity_area:
        row = frappe.db.get_value("Activity Area Bin Sort", {"parent": area.activity_area, "parenttype": "Activity Area", "activity": activity, "storage_bin": bin_name}, "sequence")
        if row is not None: return row
    return area.sequence or 0


def generate_walk_path(area, activity):
    """Replace the area's bin sort for an activity: aisles in order, each walked in the opposite direction of the one before (a serpentine)."""
    doc = frappe.get_doc("Activity Area", area)
    bins = frappe.get_all("Storage Bin", filters={"activity_area": area, "active": 1}, fields=["name", "aisle", "x_coordinate", "y_coordinate", "z_coordinate", "sequence"])
    if not bins: frappe.throw(_("Activity area {0} has no bins").format(area))
    aisles = {}
    for b in bins: aisles.setdefault(b.aisle or "", []).append(b)
    ordered = []
    for n, aisle in enumerate(sorted(aisles)):
        walk = sorted(aisles[aisle], key=lambda b: (flt(b.y_coordinate), flt(b.x_coordinate), flt(b.z_coordinate), b.sequence or 0, b.name), reverse=bool(n % 2))
        ordered += walk
    doc.set("bin_sort", [r for r in doc.bin_sort if r.activity != activity] + [{"activity": activity, "storage_bin": b.name, "sequence": 10 * i} for i, b in enumerate(ordered, 1)])
    doc.save(ignore_permissions=True)
    return len(ordered)


def update_order_distance(warehouse_order):
    """Walk path of a Warehouse Order: its tasks' source bins in sequence, then the last destination."""
    if not warehouse_order: return
    tasks = frappe.get_all("Warehouse Task", filters={"warehouse_order": warehouse_order, "docstatus": ["<", 2]}, fields=["source_bin", "destination_bin", "sequence", "creation"], order_by="sequence asc, creation asc")
    if not tasks: return
    path = [t.source_bin for t in tasks] + [tasks[-1].destination_bin]
    frappe.db.set_value("Warehouse Order", warehouse_order, "travel_distance", path_distance(path), update_modified=False)
