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
        if rule.strategy=="Manual Selection": frappe.throw(_("Bin determination rule {0} requires manual bin selection").format(rule.name))
        filters={"warehouse":context["warehouse"],"storage_type":rule.destination_storage_type,"active":1,"putaway_blocked":0}
        bins=frappe.get_all("Storage Bin",filters=filters,fields=["name","current_hu_count","sequence"],order_by="sequence asc")
        if not bins: continue
        bin_name=_apply_bin_strategy(rule.strategy,bins,context)
        if bin_name: return bin_name
    frappe.throw(_("No destination bin could be determined"))

def _apply_bin_strategy(strategy,bins,context):
    if strategy=="Least Utilized Bin":
        bins=sorted(bins,key=lambda x:x.current_hu_count or 0)
    elif strategy=="Bin Sequence":
        bins=sorted(bins,key=lambda x:x.sequence or 0)
    elif strategy=="First Empty Bin":
        empty=[b for b in bins if not b.current_hu_count]
        bins=sorted(empty or bins,key=lambda x:x.sequence or 0)
    elif strategy=="Addition to Existing Stock":
        occupied=set(frappe.get_all("WMS Stock Balance",filters={"storage_bin":["in",[b.name for b in bins]],"product":context.get("item"),"quantity":[">",0]},pluck="storage_bin"))
        preferred=[b for b in bins if b.name in occupied]
        bins=sorted(preferred or bins,key=lambda x:x.sequence or 0)
    return bins[0].name if bins else None
