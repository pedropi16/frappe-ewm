import frappe

from frappe_wms.services.task import _update_delivery_picking_status
from frappe_wms.services.warehouse_order import attach_task


def execute():
    # No Warehouse Queue row for activity='Cross Dock' has ever existed on this site - attach_task
    # (called when every one of these tasks was first created, from services/receipt.py's
    # find_cross_dock_demand) silently no-ops (by design, for back-compat) when determine_queue
    # finds nothing to match, leaving warehouse_order/queue blank. The RF app still lists these as
    # "Open" work (same family as the 27e29e4 Putaway fix this mirrors), but pull_next_warehouse_order
    # can never serve a task with no queue, so they've been permanently unworkable. Add the missing
    # queue first (idempotent - a site that already has one, e.g. a fresh install after this patch
    # is in the fixture, is left alone), then retry attach_task on every orphan now that a queue
    # can resolve.
    for warehouse in frappe.get_all("Warehouse Task", filters={"task_type": "Cross Dock"}, pluck="warehouse", distinct=True):
        if frappe.db.exists("Warehouse Queue", {"warehouse": warehouse, "activity": "Cross Dock"}):
            continue
        frappe.get_doc({
            "doctype": "Warehouse Queue",
            "queue_code": f"{warehouse}-CROSSDOCK-Q",
            "queue_name": f"{warehouse} Cross Dock (any storage type)",
            "warehouse": warehouse,
            "activity": "Cross Dock",
            "sequence_rule": "Priority Then FIFO",
            "active": 1,
        }).insert(ignore_permissions=True)

    orphans = frappe.get_all(
        "Warehouse Task",
        filters={"task_type": "Cross Dock", "docstatus": ["<", 2], "status": "Open", "warehouse_order": ["in", ("", None)]},
        pluck="name",
    )
    for name in orphans:
        task = frappe.get_doc("Warehouse Task", name)
        attach_task(task, frappe.generate_hash(length=10), reference_doctype="Warehouse Request", reference_name=task.warehouse_request)
        if task.warehouse_order:
            task.save(ignore_permissions=True)

    # Every delivery with an open Cross Dock reservation has been sitting at picking_status
    # "Not Started" this whole time (that field only existed as Not Started/Partially Picked/
    # Picked until this same fix added "Not Relevant" - see _update_delivery_picking_status) -
    # recompute it now so the Outbound Monitor reflects reality immediately, not only for
    # deliveries touched by some future reservation/confirmation from here on.
    deliveries = frappe.get_all("Warehouse Request", filters={"request_type": "Cross Dock", "reference_doctype": "Outbound Delivery"},
        pluck="reference_name", distinct=True)
    for delivery_name in deliveries:
        _update_delivery_picking_status(delivery_name)
