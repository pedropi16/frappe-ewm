import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.determination import determine_process_type
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.utils import require_role

def _pending_request_exists(rule_name):
    return frappe.db.exists("Warehouse Request", {
        "reference_doctype": "Replenishment Rule", "reference_name": rule_name,
        "status": ["not in", ["Completed", "Cancelled"]],
    })

def _order_related_request_exists(task_name):
    return frappe.db.exists("Warehouse Request", {
        "reference_doctype": "Warehouse Task", "reference_name": task_name,
        "status": ["not in", ["Completed", "Cancelled"]],
    })

def _current_quantity(warehouse, product, storage_bin, stock_type):
    return flt(frappe.db.get_value("WMS Stock Balance", {"warehouse": warehouse, "product": product, "storage_bin": storage_bin, "stock_type": stock_type}, "quantity"))

def _best_source_bin(warehouse, product, storage_type, stock_type, exclude_bin):
    bins_in_type = frappe.get_all("Storage Bin", filters={"warehouse": warehouse, "storage_type": storage_type, "removal_blocked": 0}, pluck="name")
    if not bins_in_type: return None, 0
    balances = frappe.get_all("WMS Stock Balance", filters={
        "warehouse": warehouse, "product": product, "stock_type": stock_type,
        "storage_bin": ["in", bins_in_type], "available_quantity": [">", 0],
    }, fields=["storage_bin", "handling_unit", "available_quantity"], order_by="available_quantity desc")
    balances = [b for b in balances if b.storage_bin != exclude_bin]
    if not balances: return None, 0
    return balances[0], balances[0].available_quantity

def check_replenishment_needs():
    created = []
    for rule in frappe.get_all("Replenishment Rule", filters={"active": 1}, fields=["*"]):
        current = _current_quantity(rule.warehouse, rule.product, rule.storage_bin, rule.stock_type)
        if current > rule.minimum_quantity: continue
        if _pending_request_exists(rule.name): continue
        source, available = _best_source_bin(rule.warehouse, rule.product, rule.source_storage_type, rule.stock_type, rule.storage_bin)
        if not source or available <= 0: continue
        needed = flt(rule.target_quantity) - current
        qty = min(needed, available)
        if qty <= 0: continue
        stock_uom = frappe.db.get_value("WMS Product", {"item": rule.product}, "stock_uom") or frappe.db.get_value("Item", rule.product, "stock_uom")
        process_type = determine_process_type(rule.warehouse, "Replenish", item=rule.product, stock_type=rule.stock_type, default="REPLENISH")
        request = frappe.get_doc({
            "doctype": "Warehouse Request", "request_type": "Replenish", "warehouse": rule.warehouse, "product": rule.product,
            "requested_quantity": qty, "stock_uom": stock_uom, "source_bin": source.storage_bin, "source_hu": source.handling_unit,
            "destination_bin": rule.storage_bin, "stock_type": rule.stock_type, "reference_doctype": "Replenishment Rule",
            "reference_name": rule.name, "process_type": process_type, "priority": rule.priority or "Normal", "status": "Open",
        })
        request.insert(ignore_permissions=True)
        task = create_tasks_for_request(request.name)
        created.append(task)
    return created

def create_order_related_replenishment(task):
    # Reactive replenishment triggered by a Pick task that just went through a pick denial
    # (found less stock than planned). Best-effort: if there's no Replenishment Rule
    # configured for the bin the picker was working, or no source stock is available to
    # pull from, there's nothing sensible to trigger - skip silently rather than raise and
    # unwind the pick denial that already succeeded.
    if _order_related_request_exists(task.name): return None
    rule = frappe.db.get_value("Replenishment Rule", {
        "warehouse": task.warehouse, "product": task.product, "storage_bin": task.source_bin, "active": 1,
    }, ["name", "source_storage_type", "target_quantity", "stock_type"], as_dict=True)
    if not rule: return None
    stock_type = rule.stock_type or task.stock_type_from
    current = _current_quantity(task.warehouse, task.product, task.source_bin, stock_type)
    source, available = _best_source_bin(task.warehouse, task.product, rule.source_storage_type, stock_type, task.source_bin)
    if not source or available <= 0: return None
    needed = flt(rule.target_quantity) - current
    qty = min(needed, available) if needed > 0 else available
    if qty <= 0: return None
    process_type = determine_process_type(task.warehouse, "Replenish", item=task.product, stock_type=stock_type, default="REPLENISH")
    request = frappe.get_doc({
        "doctype": "Warehouse Request", "request_type": "Replenish", "warehouse": task.warehouse, "product": task.product,
        "requested_quantity": qty, "stock_uom": task.stock_uom, "source_bin": source.storage_bin, "source_hu": source.handling_unit,
        "destination_bin": task.source_bin, "stock_type": stock_type, "reference_doctype": "Warehouse Task",
        "reference_name": task.name, "process_type": process_type, "priority": "High", "status": "Open",
    })
    request.insert(ignore_permissions=True)
    create_tasks_for_request(request.name)
    return request.name

def _create_replenishment_request(warehouse, product, storage_bin, stock_type, quantity, source_storage_type, *,
        reference_doctype="User", reference_name=None, reference_line=None, priority="Normal"):
    # Shared by direct (operator-triggered) replenishment and any other "pull stock from
    # storage into this specific bin" need with its own reference document - Work Order
    # material staging (production supply) reuses this exact mechanism rather than a
    # parallel one, since the underlying movement is identical.
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Quantity must be greater than zero"))
    source, available = _best_source_bin(warehouse, product, source_storage_type, stock_type, storage_bin)
    if not source or available <= 0: frappe.throw(_("No source stock available in storage type {0}").format(source_storage_type))
    qty = min(quantity, available)
    stock_uom = frappe.db.get_value("WMS Product", {"item": product}, "stock_uom") or frappe.db.get_value("Item", product, "stock_uom")
    process_type = determine_process_type(warehouse, "Replenish", item=product, stock_type=stock_type, default="REPLENISH")
    request = frappe.get_doc({
        "doctype": "Warehouse Request", "request_type": "Replenish", "warehouse": warehouse, "product": product,
        "requested_quantity": qty, "stock_uom": stock_uom, "source_bin": source.storage_bin, "source_hu": source.handling_unit,
        "destination_bin": storage_bin, "stock_type": stock_type, "reference_doctype": reference_doctype,
        "reference_name": reference_name or frappe.session.user, "reference_line": reference_line,
        "process_type": process_type, "priority": priority, "status": "Open",
    })
    request.insert(ignore_permissions=True)
    task = create_tasks_for_request(request.name)
    return {"warehouse_request": request.name, "task": task}

def request_direct_replenishment(warehouse, product, storage_bin, stock_type, quantity, source_storage_type):
    # Operator-triggered replenishment with no minimum-quantity threshold to clear, unlike
    # the scheduled Replenishment Rule scan - the operator is looking at the bin right now
    # and has decided it needs stock.
    require_role("WMS Operator", "WMS Supervisor")
    return _create_replenishment_request(warehouse, product, storage_bin, stock_type, quantity, source_storage_type)
