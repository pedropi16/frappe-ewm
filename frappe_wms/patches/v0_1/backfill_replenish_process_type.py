import frappe

def execute():
    if frappe.db.exists("Warehouse Process Type", "REPLENISH"):
        return
    frappe.get_doc({
        "doctype": "Warehouse Process Type", "process_type_code": "REPLENISH", "process_type_name": "Pick Face Replenishment",
        "activity": "Putaway", "source_required": 1, "destination_required": 1, "stock_required": 1,
        "confirmation_mode": "Handling Unit", "movement_type": "201", "active": 1,
    }).insert(ignore_permissions=True)
