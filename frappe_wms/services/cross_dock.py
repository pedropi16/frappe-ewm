import frappe
from frappe.utils import flt

def find_cross_dock_demand(warehouse, item, stock_type, quantity, owner=None, party=None):
    # Earliest-need-first across every open, unallocated line for this product/stock_type in
    # the warehouse - a genuinely new query, nothing like it existed before this. Only
    # deliveries with a staging_bin already resolved are candidates, since that's where the
    # incoming stock would be routed directly.
    rows = frappe.db.sql("""
        select di.name as delivery_item, di.parent as delivery, d.staging_bin,
            (di.requested_quantity - di.allocated_quantity) as outstanding
        from `tabOutbound Delivery Item` di
        join `tabOutbound Delivery` d on d.name = di.parent
        where d.warehouse=%(warehouse)s and d.docstatus=1 and di.item=%(item)s
            and di.required_stock_type=%(stock_type)s and di.requested_quantity > di.allocated_quantity
            and d.staging_bin is not null and d.staging_bin != ''
            and ifnull(d.stock_owner, '') = %(owner)s and ifnull(d.entitled_party, '') = %(party)s
            and ifnull(di.required_country_of_origin, '') = ''
        order by d.delivery_date asc, d.creation asc
    """, {"warehouse": warehouse, "item": item, "stock_type": stock_type, "owner": owner or "", "party": party or ""}, as_dict=True)
    matched = []
    remaining = flt(quantity)
    for row in rows:
        if remaining <= 0: break
        # Candidates come from a plain (snapshot) read, so every delivery matched here is also
        # visible to Frappe's own link validation when the Cross Dock request is inserted - a
        # "for update" read of the whole query also returned deliveries committed after this
        # transaction's snapshot, which then failed that validation ("Could not find Reference
        # Name"). The line itself is then locked and its open quantity re-read current, so a
        # concurrent release/receipt can't claim the same demand (see reserve_cross_dock_demand).
        current = frappe.db.sql("select requested_quantity - allocated_quantity from `tabOutbound Delivery Item` where name=%s for update", row.delivery_item)
        row.outstanding = flt(current[0][0]) if current else 0
        take = min(remaining, flt(row.outstanding))
        if take <= 0: continue
        matched.append({"delivery": row.delivery, "delivery_item": row.delivery_item, "staging_bin": row.staging_bin, "quantity": take})
        remaining -= take
    return matched


def reserve_cross_dock_demand(match):
    # Claim the matched quantity on the delivery line the moment the Cross Dock request is raised,
    # not when its task is finally confirmed. Until then the line still looked unallocated, so a
    # wave/release in between allocated and picked it from stock as well, and a second receipt
    # of the same item could cross-dock the same demand again - reproduced in a simulated shift
    # as delivery lines picked twice over (5 requested, 10 allocated and picked). The rows were
    # read "for update" by find_cross_dock_demand, so nothing else can claim them meanwhile.
    frappe.db.sql("update `tabOutbound Delivery Item` set allocated_quantity = allocated_quantity + %s where name = %s",
        (flt(match["quantity"]), match["delivery_item"]))
    parent = match["delivery"]
    rows = frappe.get_all("Outbound Delivery Item", filters={"parent": parent}, fields=["requested_quantity", "allocated_quantity"])
    if all(flt(r.allocated_quantity) >= flt(r.requested_quantity) for r in rows): status = "Fully Allocated"
    elif any(flt(r.allocated_quantity) > 0 for r in rows): status = "Partially Allocated"
    else: status = "Not Allocated"
    frappe.db.set_value("Outbound Delivery", parent, "allocation_status", status)
    # The reservation alone (before its Cross Dock task is even created, let alone confirmed) is
    # enough to know this delivery will never go through a normal Pick for this quantity - update
    # picking_status right away instead of leaving it misleadingly at "Not Started" in the
    # meantime (see _update_delivery_picking_status's own comment).
    from frappe_wms.services.task import _update_delivery_picking_status
    _update_delivery_picking_status(parent)


def redirect_cross_dock_to_putaway(delivery_name):
    """The delivery a receipt was being cross-docked to is gone (cancelled or completed short):
    its open Cross Dock work is cancelled and whatever has not moved yet is put away normally
    instead of travelling to a staging lane for a delivery that no longer needs it."""
    from frappe_wms.services.determination import determine_process_type
    from frappe_wms.services.task import plan_requests
    from frappe_wms.services.warehouse_order import release_next_in_sequence, sync_warehouse_order
    cancel_cross_dock_plan(outbound_delivery=delivery_name)  # planned (not yet received) demand of this delivery
    requests = frappe.get_all("Warehouse Request", filters={"request_type": "Cross Dock", "reference_doctype": "Outbound Delivery",
        "reference_name": delivery_name, "status": ["not in", ["Completed", "Cancelled"]]}, pluck="name")
    created = []
    for name in requests:
        req = frappe.get_doc("Warehouse Request", name, for_update=True)
        tasks = frappe.get_all("Warehouse Task", filters={"warehouse_request": name, "docstatus": ["<", 2]},
                               fields=["name", "docstatus", "confirmed_quantity", "warehouse_order"])
        moved = sum(flt(t.confirmed_quantity) for t in tasks)
        orders = set()
        for t in tasks:
            if t.docstatus == 0:
                frappe.db.set_value("Warehouse Task", t.name, {"status": "Cancelled", "docstatus": 2}, update_modified=True)
                if t.warehouse_order: orders.add(t.warehouse_order)
        for wo in orders:
            release_next_in_sequence(wo)
            sync_warehouse_order(wo)
        req.db_set("status", "Cancelled", update_modified=True)
        open_qty = flt(req.requested_quantity) - moved
        if open_qty <= 0.000001: continue
        putaway = frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Putaway", "warehouse": req.warehouse, "product": req.product,
            "requested_quantity": open_qty, "stock_uom": req.stock_uom, "source_bin": req.source_bin, "source_hu": req.source_hu,
            "stock_type": req.stock_type, "batch_no": req.batch_no, "serial_no": req.serial_no,
            "reference_doctype": "Warehouse Request", "reference_name": req.name,
            "process_type": determine_process_type(req.warehouse, "Putaway", item=req.product, stock_type=req.stock_type, default="GR_PUTAWAY"),
            "priority": "High", "status": "Open"}).insert(ignore_permissions=True)
        plan_requests([putaway.name])
        created.append(putaway.name)
    return created


# ------------------------------------------------------------------ planned cross-docking

def plan_cross_dock(inbound_delivery):
    """Planned cross-docking: before the goods arrive, match the expected quantity of an inbound delivery against open outbound demand and reserve
    it. The goods receipt then sends exactly that stock to the delivery's staging bin (create_putaway_requests); the rest is put away / matched
    opportunistically as before. Running it again plans only what is not planned yet."""
    from frappe_wms.utils import require_role
    require_role("WMS Operator", "WMS Supervisor")
    doc = frappe.get_doc("Inbound Delivery", inbound_delivery, for_update=True)
    if doc.docstatus != 1 or doc.closed_short: frappe.throw(frappe._("Only an open, submitted inbound delivery can be planned"))
    planned = []
    for row in doc.items:
        already = sum(flt(p.planned_quantity) - flt(p.consumed_quantity) for p in doc.cross_dock_plan if p.inbound_delivery_item == row.name and p.status == "Planned")
        open_qty = flt(row.expected_quantity) - flt(row.received_quantity) - already
        if open_qty <= 0.000001: continue
        for match in find_cross_dock_demand(doc.warehouse, row.item, row.expected_stock_type or "AVAILABLE", open_qty, doc.get("stock_owner"), doc.get("entitled_party")):
            reserve_cross_dock_demand(match)
            frappe.get_doc({"doctype": "Inbound Cross Dock Plan", "parent": doc.name, "parenttype": "Inbound Delivery", "parentfield": "cross_dock_plan", "inbound_delivery_item": row.name,
                "item": row.item, "outbound_delivery": match["delivery"], "outbound_delivery_item": match["delivery_item"], "staging_bin": match["staging_bin"],
                "planned_quantity": match["quantity"], "consumed_quantity": 0, "status": "Planned", "idx": len(doc.cross_dock_plan) + len(planned) + 1}).insert(ignore_permissions=True)
            planned.append(match)
    return planned


def planned_matches(inbound_delivery_item, quantity):
    """The planned matches a receipt row consumes (up to quantity): [(plan row name, match)]; the reservation was made when it was planned."""
    out, left = [], flt(quantity)
    for p in frappe.get_all("Inbound Cross Dock Plan", filters={"inbound_delivery_item": inbound_delivery_item, "status": "Planned"}, fields=["name", "outbound_delivery", "outbound_delivery_item", "staging_bin", "planned_quantity", "consumed_quantity"], order_by="idx asc"):
        take = min(left, flt(p.planned_quantity) - flt(p.consumed_quantity))
        if take <= 0: continue
        out.append((p.name, {"delivery": p.outbound_delivery, "delivery_item": p.outbound_delivery_item, "staging_bin": p.staging_bin, "quantity": take}))
        left -= take
        if left <= 0: break
    return out


def consume_plan(plan_name, quantity):
    plan = frappe.db.get_value("Inbound Cross Dock Plan", plan_name, ["planned_quantity", "consumed_quantity"], as_dict=True)
    consumed = flt(plan.consumed_quantity) + flt(quantity)
    frappe.db.set_value("Inbound Cross Dock Plan", plan_name, {"consumed_quantity": consumed, "status": "Done" if consumed >= flt(plan.planned_quantity) - 0.000001 else "Planned"})


def cancel_cross_dock_plan(inbound_delivery=None, outbound_delivery=None):
    """The inbound delivery will not bring (all of) the goods, or the outbound delivery is gone: give the reserved demand back."""
    filters = {"status": "Planned", **({"parent": inbound_delivery} if inbound_delivery else {}), **({"outbound_delivery": outbound_delivery} if outbound_delivery else {})}
    for p in frappe.get_all("Inbound Cross Dock Plan", filters=filters, fields=["name", "outbound_delivery", "outbound_delivery_item", "planned_quantity", "consumed_quantity"]):
        open_qty = flt(p.planned_quantity) - flt(p.consumed_quantity)
        if open_qty > 0 and not outbound_delivery:  # an outbound delivery that is gone takes its reservation with it
            frappe.db.sql("update `tabOutbound Delivery Item` set allocated_quantity = greatest(allocated_quantity - %s, 0) where name = %s", (open_qty, p.outbound_delivery_item))
        frappe.db.set_value("Inbound Cross Dock Plan", p.name, "status", "Cancelled")
