import frappe
from frappe.utils import cint

OPEN_TASK_STATUSES = ("Open", "Available", "Assigned", "In Process", "Partially Confirmed")

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
    }

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
