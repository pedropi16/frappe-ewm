import frappe

def execute():
    if frappe.db.exists("Warehouse Process Type", "DECON"):
        return
    frappe.get_doc({
        "doctype": "Warehouse Process Type", "process_type_code": "DECON", "process_type_name": "Deconsolidation",
        "activity": "Deconsolidation", "source_required": 1, "destination_required": 1, "stock_required": 1,
        "confirmation_mode": "Handling Unit", "movement_type": "301", "active": 1,
    }).insert(ignore_permissions=True)
