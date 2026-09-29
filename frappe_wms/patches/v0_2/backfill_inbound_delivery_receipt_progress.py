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
            # frappe.db.set_value throughout, never doc.save(): Inbound Delivery is itself
            # submittable, and a receipt against an already-submitted one (rare, but confirmed to
            # exist in production - INB-00000001) hits Frappe's own "not allowed to change Status
            # after submission" guard on a plain .save() - these are tracking-only fields.
            all_received, any_received = True, False
            for row in frappe.get_all("Inbound Delivery Item", filters={"parent": name}, fields=["name", "expected_quantity", "received_quantity"]):
                new_received = received_by_row.get(row.name, flt(row.received_quantity))
                if row.name in received_by_row:
                    new_status = "Received" if new_received >= flt(row.expected_quantity) else ("Partially Received" if new_received > 0 else "Open")
                    frappe.db.set_value("Inbound Delivery Item", row.name, {"received_quantity": new_received, "status": new_status}, update_modified=False)
                all_received = all_received and new_received >= flt(row.expected_quantity)
                any_received = any_received or new_received > 0
            if all_received:
                frappe.db.set_value("Inbound Delivery", name, {"receipt_status": "Fully Received", "status": "Received"}, update_modified=False)
            elif any_received:
                frappe.db.set_value("Inbound Delivery", name, {"receipt_status": "Partially Received", "status": "Partially Received"}, update_modified=False)
        except Exception:
            # One delivery's unrelated data problem must not abort the backfill for every other
            # delivery - this patch only needs to be best-effort across historical records.
            frappe.db.rollback()
            frappe.log_error(title="backfill_inbound_delivery_receipt_progress", message=frappe.get_traceback())
