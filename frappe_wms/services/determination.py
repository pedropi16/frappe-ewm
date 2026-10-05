import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.bin_rules import bin_violations, live_hu_count, storage_type_quantity_violation

def determine_storage_process(context):
    rules=frappe.get_all("Process Determination Rule",filters={"active":1,"warehouse":context["warehouse"]},fields=["*"],order_by="priority asc")
    for rule in rules:
        checks=("document_type","item","item_group","stock_type","source_storage_type","destination_storage_type","route")
        if all(not rule.get(key) or rule.get(key)==context.get(key) for key in checks): return rule.storage_process
    frappe.throw(_("No storage process determination rule matched"))

def matches_inspection_rule(warehouse, item, item_group=None):
    rules = frappe.get_all("Inspection Rule", filters={"active": 1}, fields=["name", "warehouse", "item", "item_group"], order_by="priority asc")
    for rule in rules:
        if rule.warehouse and rule.warehouse != warehouse: continue
        if rule.item and rule.item != item: continue
        if rule.item_group and rule.item_group != item_group: continue
        return rule.name
    return None

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

def product_indicators(item, warehouse):
    """Putaway/removal control and storage section indicators of a product: its warehouse row first, then the product."""
    fields = ("putaway_control_indicator", "stock_removal_control_indicator", "storage_section_indicator")
    if not item: return frappe._dict()
    row = wms_product_warehouse(item, warehouse)
    product = frappe.db.get_value("WMS Product", {"item": item}, list(fields), as_dict=True) or {}
    return frappe._dict({f: (row and row.get(f)) or product.get(f) for f in fields})

def search_sequence_for(warehouse, direction, indicator_field, indicator, stock_type):
    """The Storage Type Search Sequence of a control indicator: one for the exact stock type, else one without a stock type."""
    if not indicator: return None
    rows = frappe.get_all("Storage Type Search Sequence", filters={"warehouse": warehouse, "direction": direction, indicator_field: indicator, "active": 1}, fields=["name", "stock_type"], order_by="creation asc")
    exact = [r.name for r in rows if stock_type and r.stock_type == stock_type]
    return (exact or [r.name for r in rows if not r.stock_type] or [None])[0]

def determine_packaging_spec(item, customer=None, supplier=None):
    """Condition technique: the packaging spec of a product for a customer, else for a supplier, else the generic one."""
    conditions = ([{"customer": customer}] if customer else []) + ([{"supplier": supplier}] if supplier else []) + [{"customer": ["in", ["", None]], "supplier": ["in", ["", None]]}]
    for condition in conditions:
        name = frappe.db.get_value("Packaging Spec", {"item": item, "active": 1, **condition})
        if name: return name
    return None

def _preferred_storage_type(item, warehouse):
    per_warehouse = wms_product_warehouse(item, warehouse)
    if per_warehouse and per_warehouse.preferred_storage_type:
        return per_warehouse.preferred_storage_type
    return frappe.db.get_value("WMS Product", item, "preferred_storage_type")

def _search_sequence_storage_types(search_sequence, stock_type):
    seq = frappe.get_cached_doc("Storage Type Search Sequence", search_sequence)
    rows = [r for r in seq.storage_types if not seq.stock_type or seq.stock_type == stock_type]
    return [r.storage_type for r in sorted(rows, key=lambda r: r.sequence or 0)]

def _sections_by_indicator(storage_type):
    return {s.name: s.section_indicator for s in frappe.get_all("Storage Section", filters={"storage_type": storage_type}, fields=["name", "section_indicator"])}

def _filter_by_section_indicator(bins, storage_type, indicator):
    """A section carrying an indicator only takes products with it; a product with the indicator prefers those sections, then sections without one."""
    indicators = _sections_by_indicator(storage_type)
    if not any(indicators.values()): return bins
    section_of = lambda b: indicators.get(b.storage_section) if b.storage_section else None  # noqa: E731
    matching = [b for b in bins if indicator and section_of(b) == indicator]
    return matching or [b for b in bins if not section_of(b)]

def _candidate_bins_for_storage_type(warehouse, storage_type, section, context, group=None):
    filters={"warehouse":warehouse,"storage_type":storage_type,"active":1,"putaway_blocked":0}
    if section: filters["storage_section"]=section
    if group: filters["storage_group"]=group
    if context.get("item") and context.get("incoming_quantity") and storage_type_quantity_violation(context["item"], warehouse, storage_type, context["incoming_quantity"]): return []
    bins=frappe.get_all("Storage Bin",filters=filters,
        fields=["name","maximum_hus","maximum_weight","sequence","aisle","storage_section"],order_by="sequence asc")
    bins=_filter_by_section_indicator(bins, storage_type, context.get("section_indicator"))
    # reserved_hu_counts: a same-call, not-yet-posted count of HUs already assigned to a bin by
    # an earlier chunk of the same oversized request (task.py's full-pallet task splitting).
    # live_hu_count only sees what's actually posted, so without this, splitting a large
    # quantity into several full-pallet chunks within one call would rank (and pass capacity
    # checks against) the very same "emptiest"/"first empty" bin for every chunk, ignoring the
    # chunks it just handed that same bin moments earlier in this same loop.
    reserved = context.get("reserved_hu_counts") or {}
    # Ranking strategies (Least Utilized Bin, Bulk, First Empty Bin) need each bin's real,
    # right-now fill level, not the hourly-stale cached field - two putaways within the same
    # hour used to both rank the same bin as "emptiest" (see bin_rules.live_hu_count).
    for b in bins: b.current_hu_count = live_hu_count(b.name) + reserved.get(b.name, 0)
    return [b for b in bins if not bin_violations(b.name, item=context.get("item"), stock_type=context.get("stock_type"),
        hu_type=context.get("hu_type"), batch_no=context.get("batch_no"), destination_hu=context.get("destination_hu"),
        incoming_weight=context.get("incoming_weight"), incoming_volume=context.get("incoming_volume"), incoming_quantity=context.get("incoming_quantity"),
        incoming_hu_count=flt(context.get("incoming_hu_count", 1)) + reserved.get(b.name, 0))]

def _putaway_storage_types(context, rule=None):
    """Storage types to search, strongest source first: the rule's own type or sequence, the process type's default, the product's
    putaway control indicator (its Storage Type Search Sequence), then the product's preferred storage type."""
    if rule and rule.destination_storage_type: return [rule.destination_storage_type]
    if rule and rule.search_sequence: return _search_sequence_storage_types(rule.search_sequence, context.get("stock_type"))
    if context.get("forced_storage_type"): return [context["forced_storage_type"]]
    item = context.get("item")
    if not item: return []
    sequence = search_sequence_for(context["warehouse"], "Putaway", "putaway_control_indicator", context.get("_indicators", {}).get("putaway_control_indicator"), context.get("stock_type"))
    if sequence: return _search_sequence_storage_types(sequence, context.get("stock_type"))
    # Falls back to the product's preferred storage type when nothing fixes one - also the fix for a latent bug:
    # destination_storage_type isn't reqd on Bin Determination Rule, but a blank value used to make the Storage Bin
    # filter become "storage_type IS NULL", which can never match, so a rule left without one was dead code.
    preferred = _preferred_storage_type(item, context["warehouse"])
    return [preferred] if preferred else []

def determine_destination_bin(context):
    if context.get("item"): context["_indicators"] = product_indicators(context["item"], context["warehouse"]); context["section_indicator"] = context["_indicators"].get("storage_section_indicator")
    rules=frappe.get_all("Bin Determination Rule",filters={"active":1,"warehouse":context["warehouse"],"activity":context["activity"]},fields=["*"],order_by="priority asc")
    for rule in rules:
        checks=("item","item_group","stock_type","hu_type","source_storage_type")
        if not all(not rule.get(key) or rule.get(key)==context.get(key) for key in checks): continue
        if rule.fixed_destination_bin: return rule.fixed_destination_bin
        if rule.strategy=="Manual Selection": frappe.throw(_("Bin determination rule {0} requires manual bin selection").format(rule.name))

        if rule.strategy=="Near Fixed Bin" and context.get("item"):
            per_warehouse = wms_product_warehouse(context["item"], context["warehouse"])
            context["_fixed_bin"] = per_warehouse.fixed_bin if per_warehouse else None

        for storage_type in _putaway_storage_types(context, rule):
            # A search sequence tries each storage type in turn, moving to the next only if
            # the current one has zero usable bins - a single destination_storage_type or the
            # preferred_storage_type fallback are just one-element sequences of this same loop.
            bins = _candidate_bins_for_storage_type(context["warehouse"], storage_type, rule.destination_section, context, rule.destination_storage_group)
            if not bins: continue
            context["_custom_strategy"] = rule.get("custom_strategy")
            bin_name=_apply_bin_strategy(rule.strategy,bins,context)
            if bin_name: return bin_name
    # No rule gave a bin: the storage types the product's indicators lead to, each with its own default putaway strategy.
    for storage_type in _putaway_storage_types(context):
        strategy = frappe.db.get_value("Storage Type", storage_type, "putaway_strategy")
        if not strategy: continue
        bins = _candidate_bins_for_storage_type(context["warehouse"], storage_type, None, context)
        if bins and (bin_name := _apply_bin_strategy(strategy, bins, context)): return bin_name
    frappe.throw(_("No destination bin could be determined"))

def determine_route(warehouse, carrier=None):
    filters = {"active": 1, "origin_warehouse": warehouse}
    if carrier:
        route = frappe.db.get_value("WMS Route", {**filters, "carrier": carrier}, "name")
        if route: return route
    return frappe.db.get_value("WMS Route", {**filters, "carrier": ["in", ["", None]]}, "name", order_by="route_code asc")

def _occupied_bins(bins, item):
    return set(frappe.get_all("WMS Stock Balance",filters={"storage_bin":["in",[b.name for b in bins]],"product":item,"quantity":[">",0]},pluck="storage_bin"))

def get_putaway_strategies():
    """Putaway strategies other apps register under hooks.py wms_putaway_strategies: name -> function (bins, context) -> bins, best first
    (the extension point of SAP's putaway strategy BAdI). The last app to register a name wins."""
    registry = {}
    for name, paths in (frappe.get_hooks("wms_putaway_strategies") or {}).items():
        path = paths[-1] if isinstance(paths, list) else paths
        registry[name] = frappe.get_attr(path) if isinstance(path, str) else path
    return registry

def _apply_bin_strategy(strategy,bins,context):
    if strategy=="Custom":
        fn = get_putaway_strategies().get(context.get("_custom_strategy"))
        if not fn: frappe.throw(_("Unknown putaway strategy: {0}").format(context.get("_custom_strategy")))
        ranked = fn(bins, context)
        return ranked[0].name if ranked else None
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
