import frappe
from frappe.utils import flt

def _matching_labor_standard(standards, warehouse, task_type, item_group):
    for s in standards:
        if s.warehouse and s.warehouse != warehouse: continue
        if s.task_type != task_type: continue
        if s.item_group and s.item_group != item_group: continue
        return s
    return None

def warehouse_kpis(warehouse, from_date=None, to_date=None):
    task_row = frappe.db.sql("""
        select count(*), avg(timestampdiff(second, started_at, confirmed_at))
        from `tabWarehouse Task`
        where warehouse=%(warehouse)s and status='Confirmed'
            and (%(from_date)s is null or date(confirmed_at) >= %(from_date)s)
            and (%(to_date)s is null or date(confirmed_at) <= %(to_date)s)
    """, {"warehouse": warehouse, "from_date": from_date, "to_date": to_date})[0]
    throughput_total, avg_task_cycle_seconds = task_row

    days_row = frappe.db.sql("""
        select count(distinct date(confirmed_at))
        from `tabWarehouse Task`
        where warehouse=%(warehouse)s and status='Confirmed'
            and (%(from_date)s is null or date(confirmed_at) >= %(from_date)s)
            and (%(to_date)s is null or date(confirmed_at) <= %(to_date)s)
    """, {"warehouse": warehouse, "from_date": from_date, "to_date": to_date})[0]
    active_days = days_row[0] or 0

    exception_row = frappe.db.sql("""
        select sum(case when status='Exception' then 1 else 0 end),
            sum(case when status in ('Confirmed', 'Exception') then 1 else 0 end)
        from `tabWarehouse Task`
        where warehouse=%(warehouse)s and docstatus < 2
            and (%(from_date)s is null or date(modified) >= %(from_date)s)
            and (%(to_date)s is null or date(modified) <= %(to_date)s)
    """, {"warehouse": warehouse, "from_date": from_date, "to_date": to_date})[0]
    exceptions, exception_denominator = exception_row

    wo_row = frappe.db.sql("""
        select avg(timestampdiff(second, started_at, completed_at))
        from `tabWarehouse Order`
        where warehouse=%(warehouse)s and status='Completed'
            and (%(from_date)s is null or date(completed_at) >= %(from_date)s)
            and (%(to_date)s is null or date(completed_at) <= %(to_date)s)
    """, {"warehouse": warehouse, "from_date": from_date, "to_date": to_date})[0]
    avg_wo_cycle_seconds = wo_row[0]

    # Accuracy is scoped to lines that actually had a variance - a zero-variance line is a
    # perfect count either way and would just inflate the number without saying anything
    # about how well tolerance thresholds are being met under real uncertainty.
    accuracy_row = frappe.db.sql("""
        select sum(case when i.variance != 0 then 1 else 0 end),
            sum(case when i.variance != 0 and i.tolerance_group is null then 1 else 0 end)
        from `tabWMS Physical Inventory Count Item` i
        join `tabWMS Physical Inventory Count` p on p.name = i.parent
        where p.warehouse=%(warehouse)s and i.status='Posted'
            and (%(from_date)s is null or p.count_date >= %(from_date)s)
            and (%(to_date)s is null or p.count_date <= %(to_date)s)
    """, {"warehouse": warehouse, "from_date": from_date, "to_date": to_date})[0]
    variance_lines, within_tolerance_lines = accuracy_row

    return {
        "task_throughput_total": throughput_total or 0,
        "task_throughput_per_day": round((throughput_total or 0) / active_days, 2) if active_days else None,
        # is not None, not truthiness: a genuine 0.0 (confirmed near-instantly) is real data,
        # not "nothing to report" - a plain `if avg_..._seconds` would wrongly report None for it.
        "avg_task_cycle_time_hours": round(flt(avg_task_cycle_seconds) / 3600, 2) if avg_task_cycle_seconds is not None else None,
        "avg_wo_cycle_time_hours": round(flt(avg_wo_cycle_seconds) / 3600, 2) if avg_wo_cycle_seconds is not None else None,
        "exception_rate_percent": round(flt(exceptions) / exception_denominator * 100, 2) if exception_denominator else None,
        "count_accuracy_percent": round(flt(within_tolerance_lines) / variance_lines * 100, 2) if variance_lines else None,
    }

def resource_performance(warehouse, from_date=None, to_date=None):
    # started_at is task-creation time, not "operator picked up the task" (same approximation
    # and caveat as warehouse_kpis' avg_task_cycle_time_hours) - efficiency here measures
    # against that same clock, not true hands-on-task time.
    filters = {"warehouse": warehouse, "status": "Confirmed", "assigned_resource": ["is", "set"]}
    if from_date and to_date: filters["confirmed_at"] = ["between", [from_date, f"{to_date} 23:59:59"]]
    elif from_date: filters["confirmed_at"] = [">=", from_date]
    elif to_date: filters["confirmed_at"] = ["<=", f"{to_date} 23:59:59"]
    tasks = frappe.get_all("Warehouse Task", filters=filters,
        fields=["assigned_resource", "task_type", "product", "planned_quantity", "started_at", "confirmed_at"])
    if not tasks: return []

    products = {t.product for t in tasks if t.product}
    item_groups = {row.name: row.item_group for row in frappe.get_all("Item", filters={"name": ["in", list(products)]}, fields=["name", "item_group"])} if products else {}
    standards = frappe.get_all("Labor Standard", filters={"active": 1},
        fields=["warehouse", "task_type", "item_group", "standard_seconds_per_unit"], order_by="priority asc")

    by_resource = {}
    for t in tasks:
        bucket = by_resource.setdefault(t.assigned_resource, {"task_count": 0, "total_actual_seconds": 0.0, "planned_seconds": 0.0, "matched_actual_seconds": 0.0})
        bucket["task_count"] += 1
        actual_seconds = (t.confirmed_at - t.started_at).total_seconds() if t.started_at and t.confirmed_at else None
        if actual_seconds is not None: bucket["total_actual_seconds"] += actual_seconds
        standard = _matching_labor_standard(standards, warehouse, t.task_type, item_groups.get(t.product))
        if standard and actual_seconds is not None:
            bucket["planned_seconds"] += flt(standard.standard_seconds_per_unit) * flt(t.planned_quantity)
            bucket["matched_actual_seconds"] += actual_seconds

    return [{
        "assigned_resource": resource,
        "task_count": bucket["task_count"],
        "avg_task_cycle_time_hours": round(bucket["total_actual_seconds"] / bucket["task_count"] / 3600, 2) if bucket["task_count"] else None,
        # over 100% means faster than standard, under 100% slower - None when no task matched a configured standard.
        "efficiency_percent": round(bucket["planned_seconds"] / bucket["matched_actual_seconds"] * 100, 2) if bucket["matched_actual_seconds"] else None,
    } for resource, bucket in by_resource.items()]
