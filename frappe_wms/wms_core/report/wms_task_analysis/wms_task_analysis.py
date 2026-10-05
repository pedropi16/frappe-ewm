import frappe
from frappe import _
from frappe.utils import flt

GROUPS = {"Task Type": "t.task_type", "Resource": "coalesce(t.assigned_resource, '-')", "Day": "date(t.confirmed_at)", "Product": "coalesce(t.product, '-')"}


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.warehouse: frappe.throw(_("Choose a warehouse"))
    group = GROUPS.get(filters.group_by or "Task Type")
    conditions, values = ["t.warehouse=%(warehouse)s", "t.status='Confirmed'", "t.confirmed_at is not null"], {"warehouse": filters.warehouse}
    if filters.from_date: conditions.append("t.confirmed_at >= %(from_date)s"); values["from_date"] = filters.from_date
    if filters.to_date: conditions.append("t.confirmed_at < date_add(%(to_date)s, interval 1 day)"); values["to_date"] = filters.to_date
    if filters.task_type: conditions.append("t.task_type=%(task_type)s"); values["task_type"] = filters.task_type
    rows = frappe.db.sql(f"""select {group} as grp, count(*) as tasks, sum(t.confirmed_quantity) as quantity,
        sum(t.confirmed_quantity * coalesce(p.gross_weight_per_unit, 0)) as weight,
        avg(timestampdiff(second, t.started_at, t.confirmed_at)) as avg_seconds,
        sum(case when w.travel_distance is null then 0 else 1 end) as with_distance
        from `tabWarehouse Task` t left join `tabWMS Product` p on p.item = t.product left join `tabWarehouse Order` w on w.name = t.warehouse_order
        where {' and '.join(conditions)} group by 1 order by 1""", values, as_dict=True)
    columns = [{"fieldname": "grp", "label": _(filters.group_by or "Task Type"), "fieldtype": "Data", "width": 180},
        {"fieldname": "tasks", "label": _("Tasks"), "fieldtype": "Int", "width": 90}, {"fieldname": "quantity", "label": _("Quantity"), "fieldtype": "Float", "width": 110},
        {"fieldname": "weight", "label": _("Weight"), "fieldtype": "Float", "width": 110}, {"fieldname": "avg_minutes", "label": _("Avg Duration (min)"), "fieldtype": "Float", "width": 140}]
    data = [{"grp": str(r.grp), "tasks": r.tasks, "quantity": flt(r.quantity), "weight": flt(r.weight), "avg_minutes": round(flt(r.avg_seconds) / 60, 2)} for r in rows]
    chart = {"data": {"labels": [d["grp"] for d in data], "datasets": [{"name": _("Tasks"), "values": [d["tasks"] for d in data]}]}, "type": "bar"}
    return columns, data, None, chart
