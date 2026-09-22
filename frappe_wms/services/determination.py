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

def determine_process_type(warehouse, activity, *, item=None, item_group=None, stock_type=None, priority_level=None, default=None):
    # Every real call site today hardcodes a Warehouse Process Type literal - unlike
    # determine_storage_process (P2, opt-in, throws when nothing matches), this must never
    # break a warehouse with zero configured rules, so callers pass their historical literal
    # as `default` and get it back unchanged when no rule applies.
    indicator = None
    if item and warehouse:
        per_warehouse = wms_product_warehouse(item, warehouse)
        indicator = per_warehouse.process_type_determination_indicator if per_warehouse else None
    context = {"item": item, "item_group": item_group, "stock_type": stock_type,
        "priority_level": priority_level, "process_type_determination_indicator": indicator}
    rules=frappe.get_all("Warehouse Process Type Determination Rule",filters={"active":1,"activity":activity},fields=["*"],order_by="priority asc")
    for rule in rules:
        # warehouse is NOT reqd on this rule (unlike Bin/Removal Rule) so a blank rule can act
        # as a default that applies to every warehouse without per-warehouse configuration.
        if rule.warehouse and rule.warehouse != warehouse: continue
        checks=("item","item_group","stock_type","priority_level","process_type_determination_indicator")
        if all(not rule.get(key) or rule.get(key)==context.get(key) for key in checks): return rule.process_type
    return default

def wms_product_warehouse(item, warehouse):
    name = frappe.db.exists("WMS Product Warehouse", {"item": item, "warehouse": warehouse, "active": 1})
    return frappe.get_cached_doc("WMS Product Warehouse", name) if name else None

def _preferred_storage_type(item, warehouse):
    per_warehouse = wms_product_warehouse(item, warehouse)
    if per_warehouse and per_warehouse.preferred_storage_type:
        return per_warehouse.preferred_storage_type
    return frappe.db.get_value("WMS Product", item, "preferred_storage_type")

def _search_sequence_storage_types(search_sequence, stock_type):
    seq = frappe.get_cached_doc("Storage Type Search Sequence", search_sequence)
    rows = [r for r in seq.storage_types if not seq.stock_type or seq.stock_type == stock_type]
    return [r.storage_type for r in sorted(rows, key=lambda r: r.sequence or 0)]

def _candidate_bins_for_storage_type(warehouse, storage_type, section, context):
    filters={"warehouse":warehouse,"storage_type":storage_type,"active":1,"putaway_blocked":0}
    if section: filters["storage_section"]=section
    bins=frappe.get_all("Storage Bin",filters=filters,
        fields=["name","current_hu_count","current_weight","maximum_hus","maximum_weight","sequence","aisle"],order_by="sequence asc")
    return [b for b in bins if not bin_violations(b.name, item=context.get("item"), stock_type=context.get("stock_type"),
        hu_type=context.get("hu_type"), batch_no=context.get("batch_no"), destination_hu=context.get("destination_hu"),
        incoming_weight=context.get("incoming_weight"), incoming_hu_count=context.get("incoming_hu_count", 1))]

def determine_destination_bin(context):
    rules=frappe.get_all("Bin Determination Rule",filters={"active":1,"warehouse":context["warehouse"],"activity":context["activity"]},fields=["*"],order_by="priority asc")
    for rule in rules:
        checks=("item","item_group","stock_type","hu_type","source_storage_type")
        if not all(not rule.get(key) or rule.get(key)==context.get(key) for key in checks): continue
        if rule.fixed_destination_bin: return rule.fixed_destination_bin
        if rule.strategy=="Manual Selection": frappe.throw(_("Bin determination rule {0} requires manual bin selection").format(rule.name))

        if rule.strategy=="Near Fixed Bin" and context.get("item"):
            per_warehouse = wms_product_warehouse(context["item"], context["warehouse"])
            context["_fixed_bin"] = per_warehouse.fixed_bin if per_warehouse else None

        if rule.destination_storage_type:
            storage_types = [rule.destination_storage_type]
        elif rule.search_sequence:
            storage_types = _search_sequence_storage_types(rule.search_sequence, context.get("stock_type"))
        elif context.get("item"):
            # Falls back to the product's preferred storage type when a rule doesn't fix one -
            # also the fix for a latent bug: destination_storage_type isn't reqd on Bin
            # Determination Rule, but a blank value used to make the Storage Bin filter below
            # become "storage_type IS NULL", which can never match (Storage Bin.storage_type
            # is reqd), so any rule left without one was silently dead code.
            preferred = _preferred_storage_type(context["item"], context["warehouse"])
            storage_types = [preferred] if preferred else []
        else:
            storage_types = []

        for storage_type in storage_types:
            # A search sequence tries each storage type in turn, moving to the next only if
            # the current one has zero usable bins - a single destination_storage_type or the
            # preferred_storage_type fallback are just one-element sequences of this same loop.
            bins = _candidate_bins_for_storage_type(context["warehouse"], storage_type, rule.destination_section, context)
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

def _occupied_bins(bins, item):
    return set(frappe.get_all("WMS Stock Balance",filters={"storage_bin":["in",[b.name for b in bins]],"product":item,"quantity":[">",0]},pluck="storage_bin"))

def _apply_bin_strategy(strategy,bins,context):
    if strategy=="Least Utilized Bin":
        bins=sorted(bins,key=lambda x:x.current_hu_count or 0)
    elif strategy in ("Bin Sequence","General Storage"):
        # "General Storage" bins have no special handling once mixing/capacity are already
        # enforced by bin_violations() - this is the same deterministic order as Bin Sequence.
        bins=sorted(bins,key=lambda x:x.sequence or 0)
    elif strategy in ("First Empty Bin","Pallet"):
        # Pallet bins are one-HU-per-bin by convention; capacity enforcement (maximum_hus)
        # already makes "first empty" the correct pallet-putaway behavior once configured.
        empty=[b for b in bins if not b.current_hu_count]
        bins=sorted(empty or bins,key=lambda x:x.sequence or 0)
    elif strategy=="Addition to Existing Stock":
        occupied=_occupied_bins(bins, context.get("item"))
        preferred=[b for b in bins if b.name in occupied]
        bins=sorted(preferred or bins,key=lambda x:x.sequence or 0)
    elif strategy=="Bulk":
        # Bulk storage is about consolidating large quantities: prefer bins already holding
        # the same product, then rank by *most* remaining HU-count capacity (best fit for a
        # large incoming quantity) rather than nearest/first slot.
        occupied=_occupied_bins(bins, context.get("item"))
        preferred=[b for b in bins if b.name in occupied] or bins
        def remaining(b):
            return (flt(b.maximum_hus) - flt(b.current_hu_count)) if b.maximum_hus else float("inf")
        bins=sorted(preferred,key=lambda x:-remaining(x))
    elif strategy=="Near Fixed Bin":
        fixed = context.get("_fixed_bin")
        if fixed:
            exact=[b for b in bins if b.name==fixed]
            if exact: bins=exact
            else:
                fixed_aisle=frappe.db.get_value("Storage Bin", fixed, "aisle")
                near=[b for b in bins if fixed_aisle and b.aisle==fixed_aisle]
                bins=sorted(near or bins,key=lambda x:x.sequence or 0)
        else:
            bins=sorted(bins,key=lambda x:x.sequence or 0)
    return bins[0].name if bins else None
