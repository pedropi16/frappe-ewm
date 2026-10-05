import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.warehouse: frappe.throw(_("Choose a warehouse"))
    values = {"warehouse": filters.warehouse, "storage_type": filters.storage_type}
    rows = frappe.db.sql("""select b.storage_type, count(*) as bins, sum(b.putaway_blocked or b.removal_blocked or b.inventory_blocked) as blocked,
        sum(exists(select 1 from `tabWMS Stock Balance` s where s.storage_bin = b.name and s.quantity > 0)) as occupied,
        sum(coalesce(nullif(b.maximum_hus, 0), 0)) as capacity_hus,
        (select count(*) from `tabHandling Unit` h join `tabStorage Bin` b2 on b2.name = h.current_bin where b2.warehouse = %(warehouse)s and b2.storage_type = b.storage_type
            and h.status not in ('Shipped', 'Cancelled') and ifnull(h.parent_hu, '') = '') as hus
        from `tabStorage Bin` b where b.warehouse=%(warehouse)s and b.active=1 and (%(storage_type)s is null or b.storage_type=%(storage_type)s) group by b.storage_type order by b.storage_type""", values, as_dict=True)
    columns = [{"fieldname": "storage_type", "label": _("Storage Type"), "fieldtype": "Link", "options": "Storage Type", "width": 200},
        {"fieldname": "bins", "label": _("Bins"), "fieldtype": "Int", "width": 80}, {"fieldname": "occupied", "label": _("Occupied"), "fieldtype": "Int", "width": 90},
        {"fieldname": "empty", "label": _("Empty"), "fieldtype": "Int", "width": 80}, {"fieldname": "blocked", "label": _("Blocked"), "fieldtype": "Int", "width": 80},
        {"fieldname": "occupancy", "label": _("Occupancy %"), "fieldtype": "Percent", "width": 110}, {"fieldname": "hu_utilization", "label": _("HU Capacity Used %"), "fieldtype": "Percent", "width": 150}]
    data = [{"storage_type": r.storage_type, "bins": r.bins, "occupied": int(r.occupied or 0), "empty": r.bins - int(r.occupied or 0), "blocked": int(r.blocked or 0),
        "occupancy": round(100 * flt(r.occupied) / r.bins, 1) if r.bins else 0, "hu_utilization": round(100 * flt(r.hus) / flt(r.capacity_hus), 1) if flt(r.capacity_hus) else None} for r in rows]
    chart = {"data": {"labels": [d["storage_type"] for d in data], "datasets": [{"name": _("Occupancy %"), "values": [d["occupancy"] for d in data]}]}, "type": "bar"}
    return columns, data, None, chart
