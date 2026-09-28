import frappe

def execute():
    # after_install only ever runs once, at first install - an already-installed site never gets
    # a Warehouse Process Type added to install.py's list afterwards (same shape as the
    # consolidation/replenish/decon seed patches for v0_1). Needed for Two-Step Picking's
    # follow-up Sort task (services/task._create_sort_task_after_pick).
    if frappe.db.exists("Warehouse Process Type", "OB_SORT"):
        return
    frappe.get_doc({
        "doctype": "Warehouse Process Type", "process_type_code": "OB_SORT", "process_type_name": "Outbound Sort",
        "activity": "Sort", "source_required": 1, "destination_required": 1, "stock_required": 1,
        "confirmation_mode": "Handling Unit", "movement_type": "301", "active": 1,
    }).insert(ignore_permissions=True)
