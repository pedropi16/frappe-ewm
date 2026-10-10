import frappe

# frappe.db.add_index() is already idempotent (checks has_index() itself, uses
# ADD INDEX IF NOT EXISTS) - safe to call on every migrate, not just once.
INDEXES = {
    "WMS Stock Ledger Entry": [
        ["warehouse", "product", "storage_bin"], ["handling_unit", "product"],
        ["reference_doctype", "reference_name"], ["warehouse_task"],
    ],
    "WMS Stock Balance": [
        ["warehouse", "product", "storage_bin"], ["handling_unit", "product"], ["serial_no"],
    ],
    # the lookups every confirmation / pull / delivery screen makes (load audit 2026-10-10: these were full table scans - EXPLAIN type=ALL - at 600 tasks)
    "Warehouse Task": [["warehouse", "status", "priority"], ["warehouse_request"], ["warehouse_order"], ["stock_allocation"], ["source_hu"], ["destination_hu"], ["assigned_resource"]],
    "Stock Allocation": [["outbound_delivery"], ["stock_balance"], ["outbound_delivery_item"]],
    "Warehouse Order": [["warehouse", "activity", "status", "batch_key"], ["assigned_resource"]],
    "Warehouse Request": [["reference_doctype", "reference_name"], ["warehouse", "status"]],
    "Outbound Delivery": [["warehouse", "status"]],
    "Inbound Delivery": [["warehouse", "status"]],
    "Handling Unit": [["warehouse", "current_bin", "status"], ["parent_hu"]],
}

def ensure_indexes():
    # The original index patch (patches/v0_1/add_wms_indexes.py) was registered under
    # [pre_model_sync], which runs BEFORE DocType sync creates these tables - on a fresh
    # install, add_index() throws "table doesn't exist", the patch's own bare
    # `except Exception: pass` silently swallowed it, and Frappe's patch log then marks it
    # permanently "already run", so it could never retry even after the tables existed.
    # Confirmed live: production had none of these indexes despite the patch supposedly
    # having completed. This version runs from [post_model_sync] (tables definitely exist)
    # AND from the after_migrate hook, so a schema change here self-heals on every future
    # migrate instead of depending on a one-shot patch ever succeeding.
    for doctype, groups in INDEXES.items():
        if not frappe.db.table_exists(doctype): continue
        for fields in groups:
            frappe.db.add_index(doctype, fields, index_name="idx_" + "_".join(fields))
