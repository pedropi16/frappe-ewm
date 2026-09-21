import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.bin_rules import bin_violations

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
        destination_storage_type = rule.destination_storage_type
        if not destination_storage_type and context.get("item"):
            # Falls back to the product's preferred storage type when a rule doesn't fix one -
            # also the fix for a latent bug: destination_storage_type isn't reqd on Bin
            # Determination Rule, but a blank value used to make the Storage Bin filter below
            # become "storage_type IS NULL", which can never match (Storage Bin.storage_type
            # is reqd), so any rule left without one was silently dead code.
            destination_storage_type = frappe.db.get_value("WMS Product", context["item"], "preferred_storage_type")
        if not destination_storage_type: continue
        filters={"warehouse":context["warehouse"],"storage_type":destination_storage_type,"active":1,"putaway_blocked":0}
        bins=frappe.get_all("Storage Bin",filters=filters,
            fields=["name","current_hu_count","current_weight","maximum_hus","maximum_weight","sequence"],order_by="sequence asc")
        bins=[b for b in bins if not bin_violations(b.name, item=context.get("item"), stock_type=context.get("stock_type"),
            hu_type=context.get("hu_type"), batch_no=context.get("batch_no"), destination_hu=context.get("destination_hu"),
            incoming_weight=context.get("incoming_weight"), incoming_hu_count=context.get("incoming_hu_count", 1))]
        if not bins: continue
        bin_name=_apply_bin_strategy(rule.strategy,bins,context)
        if bin_name: return bin_name
    frappe.throw(_("No destination bin could be determined"))

def determine_route(warehouse, carrier=None):
    filters = {"active": 1, "origin_warehouse": warehouse}
    if carrier:
        route = frappe.db.get_value("WMS Route", {**filters, "carrier": carrier}, "name")
        if route: return route
    return frappe.db.get_value("WMS Route", {**filters, "carrier": ["in", ["", None]]}, "name", order_by="route_code asc")

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
