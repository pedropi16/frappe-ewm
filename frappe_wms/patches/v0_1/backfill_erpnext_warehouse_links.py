import frappe

def execute():
    for wh in frappe.get_all("WMS Warehouse", filters={"erpnext_warehouse": ["in", ["", None]]}, fields=["name", "warehouse_name", "company"]):
        if frappe.db.exists("Warehouse", {"warehouse_name": wh.warehouse_name, "company": wh.company}):
            erpnext_warehouse = frappe.db.get_value("Warehouse", {"warehouse_name": wh.warehouse_name, "company": wh.company}, "name")
        else:
            doc = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": wh.warehouse_name, "company": wh.company})
            doc.insert(ignore_permissions=True)
            erpnext_warehouse = doc.name
        frappe.db.set_value("WMS Warehouse", wh.name, "erpnext_warehouse", erpnext_warehouse, update_modified=False)
