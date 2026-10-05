import frappe
from frappe import _


def execute(filters=None):
    from frappe_wms.services.kpi import resource_performance
    filters = frappe._dict(filters or {})
    if not filters.warehouse: frappe.throw(_("Choose a warehouse"))
    rows = resource_performance(filters.warehouse, filters.from_date, filters.to_date)
    columns = [{"fieldname": "assigned_resource", "label": _("Resource"), "fieldtype": "Link", "options": "WMS Resource", "width": 180},
        {"fieldname": "task_count", "label": _("Tasks"), "fieldtype": "Int", "width": 90},
        {"fieldname": "avg_task_cycle_time_hours", "label": _("Avg Cycle (h)"), "fieldtype": "Float", "width": 120},
        {"fieldname": "efficiency_percent", "label": _("Efficiency % (vs labor standard)"), "fieldtype": "Percent", "width": 220}]
    chart = {"data": {"labels": [r["assigned_resource"] for r in rows], "datasets": [{"name": _("Tasks"), "values": [r["task_count"] for r in rows]}]}, "type": "bar"}
    return columns, rows, None, chart
