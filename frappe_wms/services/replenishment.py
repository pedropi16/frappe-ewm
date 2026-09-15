import frappe
from frappe.utils import flt
from frappe_wms.services.task import create_tasks_for_request

def _pending_request_exists(rule_name):
    return frappe.db.exists("Warehouse Request", {
        "reference_doctype": "Replenishment Rule", "reference_name": rule_name,
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
        request = frappe.get_doc({
            "doctype": "Warehouse Request", "request_type": "Replenish", "warehouse": rule.warehouse, "product": rule.product,
            "requested_quantity": qty, "stock_uom": stock_uom, "source_bin": source.storage_bin, "source_hu": source.handling_unit,
            "destination_bin": rule.storage_bin, "stock_type": rule.stock_type, "reference_doctype": "Replenishment Rule",
            "reference_name": rule.name, "process_type": "REPLENISH", "priority": rule.priority or "Normal", "status": "Open",
        })
        request.insert(ignore_permissions=True)
        task = create_tasks_for_request(request.name)
        created.append(task)
    return created
