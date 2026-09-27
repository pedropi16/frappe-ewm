import frappe
from frappe import _
from frappe.utils import cint, now_datetime, add_to_date
from frappe_wms.services.task import task_names_for_allocations
from frappe_wms.services.kpi import warehouse_kpis as _warehouse_kpis, resource_performance as _resource_performance
from frappe_wms.utils import wildcard_filter, require_wms_access

OPEN_TASK_STATUSES = ("Open", "Available", "Assigned", "In Process", "Partially Confirmed")
ALERT_AGE_HOURS = 4

@frappe.whitelist()
def get_delivery_execution_status(delivery_name):
    require_wms_access()
    # Feeds the Outbound Monitor drill-down: everything a supervisor needs to see and drive the
    # pick -> pack -> ship lifecycle of one delivery in one place, instead of only from the RF app.
    doc = frappe.get_doc("Outbound Delivery", delivery_name)
    doc.check_permission("read")
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": delivery_name}, fields=[
        "name", "product", "storage_bin", "handling_unit", "batch_no", "serial_no",
        "allocated_quantity", "picked_quantity", "status",
    ])
    task_names = list(task_names_for_allocations([a.name for a in allocations]))
    tasks = frappe.get_all("Warehouse Task", filters={"name": ["in", task_names]}, fields=[
        "name", "task_type", "status", "product", "planned_quantity", "confirmed_quantity", "stock_uom",
        "batch_no", "serial_no", "source_bin", "destination_bin", "source_hu", "destination_hu",
        "priority", "assigned_resource", "warehouse_order", "sequence",
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
    require_wms_access()
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
def stock_overview(warehouse, product=None, storage_bin=None, storage_type=None, stock_type=None, handling_unit=None,
                    batch_no=None, serial_no=None, limit=200):
    require_wms_access()
    # Current on-hand positions (WMS Stock Balance), as opposed to search_ledger's movement
    # history - EWM's "Stock Overview" node vs. its "Document Monitor".
    filters = {"warehouse": warehouse, "quantity": [">", 0]}
    if product: filters["product"] = wildcard_filter(product)
    if storage_bin: filters["storage_bin"] = wildcard_filter(storage_bin)
    if stock_type: filters["stock_type"] = stock_type
    if handling_unit: filters["handling_unit"] = wildcard_filter(handling_unit)
    if batch_no: filters["batch_no"] = wildcard_filter(batch_no)
    if serial_no: filters["serial_no"] = wildcard_filter(serial_no)
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
    require_wms_access()
    rows = frappe.db.sql(
        "select stock_type, sum(quantity), sum(allocated_quantity), sum(available_quantity), count(*) "
        "from `tabWMS Stock Balance` where warehouse=%s and quantity > 0 group by stock_type",
        warehouse,
    )
    return [{"stock_type": r[0], "quantity": r[1], "allocated_quantity": r[2], "available_quantity": r[3], "balance_rows": r[4]} for r in rows]

@frappe.whitelist()
def search_ledger(warehouse, product=None, storage_bin=None, handling_unit=None, movement_type=None,
                   batch_no=None, serial_no=None, posting_user=None, from_date=None, to_date=None, limit=100):
    require_wms_access()
    filters = {"warehouse": warehouse}
    if product: filters["product"] = wildcard_filter(product)
    if storage_bin: filters["storage_bin"] = wildcard_filter(storage_bin)
    if handling_unit: filters["handling_unit"] = wildcard_filter(handling_unit)
    if movement_type: filters["movement_type"] = movement_type
    if batch_no: filters["batch_no"] = wildcard_filter(batch_no)
    if serial_no: filters["serial_no"] = wildcard_filter(serial_no)
    if posting_user: filters["posting_user"] = wildcard_filter(posting_user)
    if from_date or to_date:
        filters["posting_datetime"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31 23:59:59"]]
    return frappe.get_list("WMS Stock Ledger Entry", filters=filters, fields=[
        "name", "posting_datetime", "product", "batch_no", "serial_no", "handling_unit", "storage_bin",
        "stock_type", "quantity", "stock_uom", "movement_type", "reference_doctype", "reference_name",
        "warehouse_task", "posting_user",
    ], order_by="posting_datetime desc", limit=cint(limit) or 100)

@frappe.whitelist()
def stock_line_history(product, stock_type, storage_bin=None, handling_unit=None, batch_no=None, serial_no=None, limit=10):
    require_wms_access()
    """The last few postings that actually built up one specific balance line - a Repack Center
    product row only ever shows the current quantity; this is "the document it's attached to"
    (what created or last touched it) that a balance row itself has no way to carry."""
    filters = {"product": product, "stock_type": stock_type}
    filters["storage_bin"] = storage_bin if storage_bin else ["in", ["", None]]
    filters["handling_unit"] = handling_unit if handling_unit else ["in", ["", None]]
    if batch_no: filters["batch_no"] = batch_no
    if serial_no: filters["serial_no"] = serial_no
    return frappe.get_list("WMS Stock Ledger Entry", filters=filters, fields=[
        "name", "posting_datetime", "quantity", "movement_type", "reference_doctype", "reference_name",
        "warehouse_task", "posting_user",
    ], order_by="posting_datetime desc", limit=cint(limit) or 10)

@frappe.whitelist()
def search_tasks(warehouse, task_type=None, status=None, product=None, source_bin=None, destination_bin=None,
                  assigned_resource=None, batch_no=None, serial_no=None, wave=None, queue=None, priority=None,
                  confirmed_by=None, from_date=None, to_date=None, limit=200):
    require_wms_access()
    filters = {"warehouse": warehouse}
    if task_type: filters["task_type"] = task_type
    if status: filters["status"] = status
    if product: filters["product"] = wildcard_filter(product)
    if source_bin: filters["source_bin"] = wildcard_filter(source_bin)
    if destination_bin: filters["destination_bin"] = wildcard_filter(destination_bin)
    if assigned_resource: filters["assigned_resource"] = wildcard_filter(assigned_resource)
    if batch_no: filters["batch_no"] = wildcard_filter(batch_no)
    if serial_no: filters["serial_no"] = wildcard_filter(serial_no)
    if wave: filters["wave"] = wildcard_filter(wave)
    if queue: filters["queue"] = wildcard_filter(queue)
    if priority: filters["priority"] = priority
    if confirmed_by: filters["confirmed_by"] = wildcard_filter(confirmed_by)
    if from_date or to_date: filters["confirmed_at"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31 23:59:59"]]
    return frappe.get_list("Warehouse Task", filters=filters, fields=[
        "name", "task_type", "product", "planned_quantity", "confirmed_quantity", "stock_uom",
        "batch_no", "serial_no", "source_bin", "destination_bin", "source_hu", "destination_hu",
        "stock_type_from", "stock_type_to", "movement_type", "priority", "status", "assigned_resource",
        "wave", "queue", "warehouse_order", "sequence", "started_at", "confirmed_at", "confirmed_by",
        "exception_code", "blocking_reason", "modified",
    ], order_by="modified desc", limit=cint(limit) or 200)

@frappe.whitelist()
def search_handling_units(warehouse, hu_number=None, status=None, hu_type=None, current_bin=None,
                           stock_status=None, outbound_delivery=None, storage_type=None, work_center=None,
                           modified_by=None, from_date=None, to_date=None, limit=200):
    require_wms_access()
    filters = {"warehouse": warehouse}
    if hu_number: filters["hu_number"] = wildcard_filter(hu_number)
    if status: filters["status"] = status
    if hu_type: filters["hu_type"] = wildcard_filter(hu_type)
    if stock_status: filters["stock_status"] = stock_status
    if outbound_delivery: filters["outbound_delivery"] = wildcard_filter(outbound_delivery)
    if modified_by: filters["modified_by"] = wildcard_filter(modified_by)
    if from_date or to_date: filters["modified"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31 23:59:59"]]
    if current_bin:
        filters["current_bin"] = wildcard_filter(current_bin)
    elif storage_type:
        bins = frappe.get_all("Storage Bin", filters={"warehouse": warehouse, "storage_type": storage_type}, pluck="name")
        filters["current_bin"] = ["in", bins or ["\x00no-such-bin\x00"]]
    if work_center:
        wc_bin = frappe.db.get_value("Work Center", work_center, "bin")
        filters["current_bin"] = wc_bin or "\x00no-such-bin\x00"
    return frappe.get_list("Handling Unit", filters=filters, fields=[
        "name", "hu_number", "hu_type", "current_bin", "parent_hu", "top_hu", "status", "stock_status",
        "outbound_delivery", "shipment", "closed", "loaded", "gross_weight", "net_weight",
        "seal_number", "external_reference", "creation", "modified",
    ], order_by="modified desc", limit=cint(limit) or 200)

@frappe.whitelist()
def search_bins(warehouse, bin_code=None, storage_type=None, work_center=None, limit=200):
    require_wms_access()
    filters = {"warehouse": warehouse}
    if bin_code: filters["name"] = wildcard_filter(bin_code)
    if storage_type: filters["storage_type"] = storage_type
    if work_center:
        wc_bin = frappe.db.get_value("Work Center", work_center, "bin")
        filters["name"] = wc_bin or "\x00no-such-bin\x00"
    return frappe.get_list("Storage Bin", filters=filters, fields=[
        "name", "bin_name", "storage_type", "storage_section", "bin_type",
        "current_hu_count", "maximum_hus", "putaway_blocked", "removal_blocked", "inventory_blocked", "active", "modified",
    ], order_by="modified desc", limit=cint(limit) or 200)

@frappe.whitelist()
def hu_ancestor_chain(hu_name):
    require_wms_access()
    """Walks parent_hu up to the top, so a search hit that's nested several levels deep can be
    shown where it actually is (its bin, then each ancestor HU in order) instead of as a bare,
    context-free row - current_bin is already correct at every level regardless of nesting depth."""
    chain = [hu_name]
    cur = frappe.db.get_value("Handling Unit", hu_name, "parent_hu")
    seen = {hu_name}
    while cur and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = frappe.db.get_value("Handling Unit", cur, "parent_hu")
    chain.reverse()
    return {"bin": frappe.db.get_value("Handling Unit", hu_name, "current_bin"), "chain": chain}

def _hu_node(hu_name):
    fields = ["name", "hu_number", "hu_type", "current_bin", "parent_hu", "top_hu", "status", "stock_status",
               "outbound_delivery", "shipment", "closed", "loaded", "gross_weight", "net_weight",
               "seal_number", "external_reference"]
    hu = frappe.db.get_value("Handling Unit", hu_name, fields, as_dict=True)
    if not hu: return None
    stock = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu_name, "quantity": [">", 0]},
        fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"], order_by="product")
    children = frappe.get_all("Handling Unit", filters={"parent_hu": hu_name}, pluck="name", order_by="hu_number")
    return {"hu": hu, "stock": stock, "children": [_hu_node(c) for c in children]}

@frappe.whitelist()
def handling_unit_tree(hu_name):
    require_wms_access()
    """Full recursive nesting (all descendants, every level) plus contents at each node -
    the flat search grid and RF hu_overview only ever show one level of children."""
    node = _hu_node(hu_name)
    if not node: frappe.throw(_("Handling Unit {0} not found").format(hu_name))
    return node

@frappe.whitelist()
def search_inbound_deliveries(warehouse, status=None, supplier=None, receipt_status=None, process_status=None,
                               inbound_delivery_number=None, receiving_bin=None, from_date=None, to_date=None, limit=200):
    require_wms_access()
    filters = {"warehouse": warehouse}
    if status: filters["status"] = status
    if supplier: filters["supplier"] = wildcard_filter(supplier)
    if receipt_status: filters["receipt_status"] = receipt_status
    if process_status: filters["process_status"] = process_status
    if inbound_delivery_number: filters["inbound_delivery_number"] = wildcard_filter(inbound_delivery_number)
    if receiving_bin: filters["receiving_bin"] = wildcard_filter(receiving_bin)
    if from_date or to_date: filters["expected_arrival"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31 23:59:59"]]
    return frappe.get_list("Inbound Delivery", filters=filters, fields=[
        "name", "inbound_delivery_number", "warehouse", "company", "supplier", "receiving_bin",
        "expected_arrival", "posting_date", "receipt_status", "process_status", "status",
        "external_reference", "modified",
    ], order_by="modified desc", limit=cint(limit) or 200)

@frappe.whitelist()
def search_outbound_deliveries(warehouse, status=None, customer=None, allocation_status=None,
                                packing_status=None, loading_status=None, priority=None,
                                outbound_delivery_number=None, route=None, staging_bin=None, door=None,
                                from_date=None, to_date=None, limit=200):
    require_wms_access()
    filters = {"warehouse": warehouse}
    if status: filters["status"] = status
    if customer: filters["customer"] = wildcard_filter(customer)
    if allocation_status: filters["allocation_status"] = allocation_status
    if packing_status: filters["packing_status"] = packing_status
    if loading_status: filters["loading_status"] = loading_status
    if priority: filters["priority"] = priority
    if outbound_delivery_number: filters["outbound_delivery_number"] = wildcard_filter(outbound_delivery_number)
    if route: filters["route"] = wildcard_filter(route)
    if staging_bin: filters["staging_bin"] = wildcard_filter(staging_bin)
    if door: filters["door"] = wildcard_filter(door)
    if from_date or to_date: filters["delivery_date"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31 23:59:59"]]
    return frappe.get_list("Outbound Delivery", filters=filters, fields=[
        "name", "outbound_delivery_number", "warehouse", "customer", "route", "delivery_date", "priority",
        "staging_bin", "door", "allocation_status", "picking_status", "packing_status", "loading_status",
        "goods_issue_status", "status", "external_reference", "modified",
    ], order_by="modified desc", limit=cint(limit) or 200)

@frappe.whitelist()
def resource_workload(warehouse):
    require_wms_access()
    resources = frappe.get_list("WMS Resource", filters={"warehouse": warehouse, "active": 1}, fields=[
        "name", "resource_code", "user", "resource_type", "resource_group", "device_id",
        "current_queue", "current_work_center", "current_bin", "logged_in_at",
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
    require_wms_access()
    filters = {"warehouse": warehouse}
    if activity: filters["activity"] = activity
    return frappe.get_list("Warehouse Queue", filters=filters, fields=[
        "name", "queue_code", "queue_name", "activity", "storage_type", "activity_area",
        "resource_group", "sequence_rule", "active",
    ], order_by="queue_code asc", limit=cint(limit) or 100)

@frappe.whitelist()
def warehouse_kpis(warehouse, from_date=None, to_date=None):
    require_wms_access()
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    return _warehouse_kpis(warehouse, from_date, to_date)

@frappe.whitelist()
def resource_performance(warehouse, from_date=None, to_date=None):
    require_wms_access()
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    return _resource_performance(warehouse, from_date, to_date)

@frappe.whitelist()
def get_alerts(warehouse):
    require_wms_access()
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    cutoff = add_to_date(now_datetime(), hours=-ALERT_AGE_HOURS)
    return {
        "pending_approval_counts": frappe.get_list("WMS Physical Inventory Count",
            filters={"warehouse": warehouse, "status": "Under Review"},
            fields=["name", "storage_bin", "storage_type", "product", "count_date", "modified"],
            order_by="modified asc", limit=50),
        "aged_exceptions": frappe.get_list("Warehouse Task",
            filters={"warehouse": warehouse, "status": "Exception", "modified": ["<", cutoff]},
            fields=["name", "task_type", "product", "source_bin", "destination_bin", "assigned_resource",
                    "exception_code", "blocking_reason", "modified"],
            order_by="modified asc", limit=50),
        "stalled_warehouse_orders": frappe.get_list("Warehouse Order",
            filters={"warehouse": warehouse, "status": "Open", "assigned_resource": ["in", ["", None]], "creation": ["<", cutoff]},
            fields=["name", "activity", "queue", "priority", "status", "task_count", "creation"],
            order_by="creation asc", limit=50),
    }

@frappe.whitelist()
def search_waves(warehouse, status=None, route=None, released_by=None, from_date=None, to_date=None, limit=100):
    require_wms_access()
    filters = {"warehouse": warehouse}
    if status: filters["status"] = status
    if route: filters["route"] = wildcard_filter(route)
    if released_by: filters["released_by"] = wildcard_filter(released_by)
    if from_date or to_date: filters["ship_date"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31 23:59:59"]]
    waves = frappe.get_list("WMS Wave", filters=filters, fields=[
        "name", "route", "ship_date", "priority", "picking_strategy", "status", "released_at", "released_by", "modified",
    ], order_by="modified desc", limit=cint(limit) or 100)
    for wave in waves:
        wave["delivery_count"] = frappe.db.count("WMS Wave Delivery", {"parent": wave.name})
    return waves
