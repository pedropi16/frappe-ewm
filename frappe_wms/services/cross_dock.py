import frappe
from frappe.utils import flt

def find_cross_dock_demand(warehouse, item, stock_type, quantity):
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
        order by d.delivery_date asc, d.creation asc
    """, {"warehouse": warehouse, "item": item, "stock_type": stock_type}, as_dict=True)
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
