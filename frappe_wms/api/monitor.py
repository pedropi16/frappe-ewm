import frappe
from frappe.utils import cint
from frappe_wms.services.task import task_names_for_allocations

OPEN_TASK_STATUSES = ("Open", "Available", "Assigned", "In Process", "Partially Confirmed")

@frappe.whitelist()
def get_delivery_execution_status(delivery_name):
    # Feeds the Outbound Monitor drill-down: everything a supervisor needs to see and drive the
    # pick -> pack -> ship lifecycle of one delivery in one place, instead of only from the RF app.
    doc = frappe.get_doc("Outbound Delivery", delivery_name)
    doc.check_permission("read")
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": delivery_name}, fields=[
        "name", "product", "storage_bin", "handling_unit", "allocated_quantity", "picked_quantity", "status",
    ])
    task_names = list(task_names_for_allocations([a.name for a in allocations]))
    tasks = frappe.get_all("Warehouse Task", filters={"name": ["in", task_names]}, fields=[
        "name", "task_type", "status", "planned_quantity", "confirmed_quantity",
        "source_bin", "destination_bin", "assigned_resource", "warehouse_order",
    ], order_by="sequence asc, creation asc") if task_names else []
    warehouse_orders = sorted({t.warehouse_order for t in tasks if t.warehouse_order})
    packing_orders = frappe.get_all("Packing Order", filters={"outbound_delivery": delivery_name}, fields=[
        "name", "status", "work_center_bin",
    ])
    goods_issues = frappe.get_all("Goods Issue", filters={"outbound_delivery": delivery_name}, fields=[
        "name", "status", "docstatus", "posting_datetime", "reversed",
    ])
    return {
        "delivery": doc.as_dict(),
        "allocations": allocations,
        "tasks": tasks,
        "warehouse_orders": warehouse_orders,
        "packing_orders": packing_orders,
        "goods_issues": goods_issues,
    }

@frappe.whitelist()
def get_summary(warehouse):
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    task_type_rows = frappe.db.sql(
        "select task_type, count(*) from `tabWarehouse Task` where warehouse=%s and status in %s and docstatus < 2 group by task_type",
        (warehouse, OPEN_TASK_STATUSES),
    )
    return {
        "open_tasks_by_type": [{"task_type": row[0], "count": row[1]} for row in task_type_rows],
        "exceptions": frappe.db.count("Warehouse Task", {"warehouse": warehouse, "status": "Exception"}),
        "pending_replenishment": frappe.db.count("Warehouse Request", {"warehouse": warehouse, "request_type": "Replenish", "status": ["not in", ["Completed", "Cancelled"]]}),
        "inbound_in_progress": frappe.db.count("Inbound Delivery", {"warehouse": warehouse, "status": ["not in", ["Completed", "Cancelled"]]}),
        "outbound_in_progress": frappe.db.count("Outbound Delivery", {"warehouse": warehouse, "status": ["not in", ["Completed", "Cancelled"]]}),
        "open_counts": frappe.db.count("WMS Physical Inventory Count", {"warehouse": warehouse, "status": ["not in", ["Posted", "Cancelled"]]}),
        "open_inspections": frappe.db.count("WMS Quality Inspection", {"warehouse": warehouse, "status": "Draft"}),
        "open_waves": frappe.db.count("WMS Wave", {"warehouse": warehouse, "status": ["not in", ["Completed", "Cancelled"]]}),
        "active_resources": frappe.db.count("WMS Resource", {"warehouse": warehouse, "active": 1}),
    }

@frappe.whitelist()
def stock_overview(warehouse, product=None, storage_bin=None, storage_type=None, stock_type=None, handling_unit=None, limit=200):
    # Current on-hand positions (WMS Stock Balance), as opposed to search_ledger's movement
    # history - EWM's "Stock Overview" node vs. its "Document Monitor".
    filters = {"warehouse": warehouse, "quantity": [">", 0]}
    if product: filters["product"] = product
    if storage_bin: filters["storage_bin"] = storage_bin
    if stock_type: filters["stock_type"] = stock_type
    if handling_unit: filters["handling_unit"] = handling_unit
    rows = frappe.get_list("WMS Stock Balance", filters=filters, fields=[
        "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type",
        "quantity", "allocated_quantity", "available_quantity", "stock_uom", "last_movement_date",
    ], order_by="storage_bin asc, product asc", limit=cint(limit) or 200)
    if storage_type:
        bins_in_type = set(frappe.get_all("Storage Bin", filters={"warehouse": warehouse, "storage_type": storage_type}, pluck="name"))
        rows = [r for r in rows if r.storage_bin in bins_in_type]
    return rows

@frappe.whitelist()
def stock_overview_summary(warehouse):
    rows = frappe.db.sql(
        "select stock_type, sum(quantity), sum(allocated_quantity), sum(available_quantity), count(*) "
        "from `tabWMS Stock Balance` where warehouse=%s and quantity > 0 group by stock_type",
        warehouse,
    )
    return [{"stock_type": r[0], "quantity": r[1], "allocated_quantity": r[2], "available_quantity": r[3], "balance_rows": r[4]} for r in rows]

@frappe.whitelist()
def search_ledger(warehouse, product=None, storage_bin=None, handling_unit=None, movement_type=None, from_date=None, to_date=None, limit=100):
    filters = {"warehouse": warehouse}
    if product: filters["product"] = product
    if storage_bin: filters["storage_bin"] = storage_bin
    if handling_unit: filters["handling_unit"] = handling_unit
    if movement_type: filters["movement_type"] = movement_type
    if from_date or to_date:
        filters["posting_datetime"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31 23:59:59"]]
    return frappe.get_list("WMS Stock Ledger Entry", filters=filters, fields=[
        "name", "posting_datetime", "product", "batch_no", "serial_no", "handling_unit", "storage_bin",
        "stock_type", "quantity", "stock_uom", "movement_type", "reference_doctype", "reference_name",
        "warehouse_task", "posting_user",
    ], order_by="posting_datetime desc", limit=cint(limit) or 100)

@frappe.whitelist()
def search_tasks(warehouse, task_type=None, status=None, product=None, source_bin=None, destination_bin=None, assigned_resource=None, limit=100):
    filters = {"warehouse": warehouse}
    if task_type: filters["task_type"] = task_type
    if status: filters["status"] = status
    if product: filters["product"] = product
    if source_bin: filters["source_bin"] = source_bin
    if destination_bin: filters["destination_bin"] = destination_bin
    if assigned_resource: filters["assigned_resource"] = assigned_resource
    return frappe.get_list("Warehouse Task", filters=filters, fields=[
        "name", "task_type", "product", "planned_quantity", "confirmed_quantity", "stock_uom",
        "source_bin", "destination_bin", "source_hu", "destination_hu", "priority", "status",
        "assigned_resource", "wave", "queue", "modified",
    ], order_by="modified desc", limit=cint(limit) or 100)

@frappe.whitelist()
def search_handling_units(warehouse, hu_number=None, status=None, hu_type=None, current_bin=None, limit=100):
    filters = {"warehouse": warehouse}
    if hu_number: filters["hu_number"] = ["like", f"%{hu_number}%"]
    if status: filters["status"] = status
    if hu_type: filters["hu_type"] = hu_type
    if current_bin: filters["current_bin"] = current_bin
    return frappe.get_list("Handling Unit", filters=filters, fields=[
        "name", "hu_number", "hu_type", "current_bin", "parent_hu", "status", "stock_status",
        "outbound_delivery", "shipment", "closed", "loaded", "modified",
    ], order_by="modified desc", limit=cint(limit) or 100)

@frappe.whitelist()
def search_inbound_deliveries(warehouse, status=None, supplier=None, limit=100):
    filters = {"warehouse": warehouse}
    if status: filters["status"] = status
    if supplier: filters["supplier"] = supplier
    return frappe.get_list("Inbound Delivery", filters=filters, fields=[
        "name", "inbound_delivery_number", "supplier", "receiving_bin", "status", "modified",
    ], order_by="modified desc", limit=cint(limit) or 100)

@frappe.whitelist()
def search_outbound_deliveries(warehouse, status=None, customer=None, limit=100):
    filters = {"warehouse": warehouse}
    if status: filters["status"] = status
    if customer: filters["customer"] = customer
    return frappe.get_list("Outbound Delivery", filters=filters, fields=[
        "name", "outbound_delivery_number", "customer", "staging_bin", "picking_status",
        "goods_issue_status", "status", "modified",
    ], order_by="modified desc", limit=cint(limit) or 100)

@frappe.whitelist()
def resource_workload(warehouse):
    resources = frappe.get_list("WMS Resource", filters={"warehouse": warehouse, "active": 1}, fields=[
        "name", "resource_code", "user", "resource_type", "resource_group", "current_queue", "current_bin",
    ], order_by="resource_code asc", limit=200)
    rows = frappe.db.sql(
        "select assigned_resource, count(*) from `tabWarehouse Task` "
        "where warehouse=%s and status in %s and docstatus < 2 and assigned_resource is not null "
        "group by assigned_resource",
        (warehouse, OPEN_TASK_STATUSES),
    )
    open_counts = dict(rows)
    for r in resources:
        r["open_tasks"] = open_counts.get(r["name"], 0)
    return resources

@frappe.whitelist()
def search_queues(warehouse, activity=None, limit=100):
    filters = {"warehouse": warehouse}
    if activity: filters["activity"] = activity
    return frappe.get_list("Warehouse Queue", filters=filters, fields=[
        "name", "queue_code", "queue_name", "activity", "storage_type", "resource_group", "active",
    ], order_by="queue_code asc", limit=cint(limit) or 100)

@frappe.whitelist()
def search_waves(warehouse, status=None, limit=100):
    filters = {"warehouse": warehouse}
    if status: filters["status"] = status
    waves = frappe.get_list("WMS Wave", filters=filters, fields=[
        "name", "route", "ship_date", "priority", "picking_strategy", "status", "released_at", "released_by", "modified",
    ], order_by="modified desc", limit=cint(limit) or 100)
    for wave in waves:
        wave["delivery_count"] = frappe.db.count("WMS Wave Delivery", {"parent": wave.name})
    return waves
