import frappe
from frappe.utils import flt

from frappe_wms.services.receipt import OPEN_INBOUND_STATUSES


def execute():
    # Backfills the effect of _update_inbound_delivery_receipt_progress (added alongside this
    # patch) for every Inbound Delivery whose real Goods Receipt history predates the fix -
    # confirmed live in production: several deliveries already had a real, submitted Goods
    # Receipt against them yet still read received_quantity=0/status="Draft" forever, so the RF
    # Receive screen kept re-offering them as open work indefinitely.
    deliveries = frappe.get_all("Inbound Delivery", filters={"status": ["in", OPEN_INBOUND_STATUSES]}, pluck="name")
    for name in deliveries:
        received_by_row = {}
        rows = frappe.db.sql("""
            select gri.inbound_delivery_item, sum(gri.quantity) qty
            from `tabGoods Receipt Item` gri
            join `tabGoods Receipt` gr on gr.name = gri.parent
            where gr.inbound_delivery = %s and gr.docstatus = 1
            group by gri.inbound_delivery_item
        """, name, as_dict=True)
        for row in rows:
            if row.inbound_delivery_item: received_by_row[row.inbound_delivery_item] = flt(row.qty)
        if not received_by_row: continue

        try:
            doc = frappe.get_doc("Inbound Delivery", name)
            for item in doc.items:
                if item.name in received_by_row:
                    item.received_quantity = received_by_row[item.name]
                if flt(item.received_quantity) >= flt(item.expected_quantity):
                    item.status = "Received"
                elif flt(item.received_quantity) > 0:
                    item.status = "Partially Received"
            if all(flt(r.received_quantity) >= flt(r.expected_quantity) for r in doc.items):
                doc.receipt_status, doc.status = "Fully Received", "Received"
            elif any(flt(r.received_quantity) > 0 for r in doc.items):
                doc.receipt_status, doc.status = "Partially Received", "Partially Received"
            doc.save(ignore_permissions=True)
        except Exception:
            # One delivery's unrelated data problem (e.g. a stale link to a since-deleted Item -
            # seen on the dev site from old e2e debris) must not abort the backfill for every
            # other delivery - this patch only needs to be best-effort across historical records.
            frappe.db.rollback()
            frappe.log_error(title="backfill_inbound_delivery_receipt_progress", message=frappe.get_traceback())
