import frappe
from frappe.utils import flt

def recalculate_stale_bin_capacity():
    bins=frappe.get_all("Storage Bin",filters={"active":1},pluck="name")
    for name in bins:
        count=frappe.db.count("Handling Unit",{"current_bin":name,"status":["not in",["Shipped","Cancelled"]]})
        weight=frappe.db.sql("select coalesce(sum(gross_weight),0) from `tabHandling Unit` where current_bin=%s and status not in ('Shipped','Cancelled')",name)[0][0]
        frappe.db.set_value("Storage Bin",name,{"current_hu_count":count,"current_weight":weight},update_modified=False)

def verify_stock_balance_integrity():
    negatives=frappe.get_all("WMS Stock Balance",filters={"quantity":["<",0]},fields=["name","warehouse","product","storage_bin","quantity"])
    if negatives: frappe.log_error("\n".join(map(str,negatives)),"WMS negative stock integrity check")

def verify_erpnext_stock_reconciliation():
    warehouses = frappe.get_all("WMS Warehouse", filters={"erpnext_warehouse": ["is", "set"]}, fields=["name", "erpnext_warehouse"])
    mismatches = []
    for wh in warehouses:
        wms_totals = frappe.db.sql(
            "select product, sum(quantity) from `tabWMS Stock Balance` where warehouse=%s group by product",
            wh.name,
        )
        for product, wms_qty in wms_totals:
            erpnext_qty = flt(frappe.db.get_value("Bin", {"warehouse": wh.erpnext_warehouse, "item_code": product}, "actual_qty"))
            if round(flt(wms_qty), 6) != round(erpnext_qty, 6):
                mismatches.append({"warehouse": wh.name, "erpnext_warehouse": wh.erpnext_warehouse, "product": product, "wms_quantity": wms_qty, "erpnext_quantity": erpnext_qty})
    if mismatches: frappe.log_error("\n".join(map(str, mismatches)), "WMS/ERPNext stock reconciliation drift")
