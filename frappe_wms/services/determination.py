import frappe
from frappe import _

def determine_storage_process(context):
    rules=frappe.get_all("Process Determination Rule",filters={"active":1,"warehouse":context["warehouse"]},fields=["*"],order_by="priority asc")
    for rule in rules:
        checks=("document_type","item","item_group","stock_type","source_storage_type","destination_storage_type","route")
        if all(not rule.get(key) or rule.get(key)==context.get(key) for key in checks): return rule.storage_process
    frappe.throw(_("No storage process determination rule matched"))

def determine_destination_bin(context):
    rules=frappe.get_all("Bin Determination Rule",filters={"active":1,"warehouse":context["warehouse"],"activity":context["activity"]},fields=["*"],order_by="priority asc")
    for rule in rules:
        checks=("item","item_group","stock_type","hu_type","source_storage_type")
        if not all(not rule.get(key) or rule.get(key)==context.get(key) for key in checks): continue
        if rule.fixed_destination_bin: return rule.fixed_destination_bin
        filters={"warehouse":context["warehouse"],"storage_type":rule.destination_storage_type,"active":1,"putaway_blocked":0}
        bins=frappe.get_all("Storage Bin",filters=filters,fields=["name","current_hu_count","sequence"],order_by="sequence asc")
        if rule.strategy=="Least Utilized Bin": bins.sort(key=lambda x:x.current_hu_count or 0)
        if bins: return bins[0].name
    frappe.throw(_("No destination bin could be determined"))
