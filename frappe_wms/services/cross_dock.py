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
        take = min(remaining, flt(row.outstanding))
        if take <= 0: continue
        matched.append({"delivery": row.delivery, "delivery_item": row.delivery_item, "staging_bin": row.staging_bin, "quantity": take})
        remaining -= take
    return matched
