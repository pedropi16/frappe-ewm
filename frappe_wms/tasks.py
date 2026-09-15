import frappe

def recalculate_stale_bin_capacity():
    bins=frappe.get_all("Storage Bin",filters={"active":1},pluck="name")
    for name in bins:
        count=frappe.db.count("Handling Unit",{"current_bin":name,"status":["not in",["Shipped","Cancelled"]]})
        weight=frappe.db.sql("select coalesce(sum(gross_weight),0) from `tabHandling Unit` where current_bin=%s and status not in ('Shipped','Cancelled')",name)[0][0]
        frappe.db.set_value("Storage Bin",name,{"current_hu_count":count,"current_weight":weight},update_modified=False)

def verify_stock_balance_integrity():
    negatives=frappe.get_all("WMS Stock Balance",filters={"quantity":["<",0]},fields=["name","warehouse","product","storage_bin","quantity"])
    if negatives: frappe.log_error("\n".join(map(str,negatives)),"WMS negative stock integrity check")
