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
    failed = []
    for name in orphans:
        frappe.db.savepoint("cross_dock_backfill")
        try:
            task = frappe.get_doc("Warehouse Task", name)
            attach_task(task, frappe.generate_hash(length=10), reference_doctype="Warehouse Request", reference_name=task.warehouse_request)
            if task.warehouse_order:
                task.save(ignore_permissions=True)
        except Exception:
            # Separate, pre-existing data problem on this site found while building this patch:
            # most of these tasks' source_hu points at a Handling Unit that was never actually
            # created (or was deleted after this task was) - a genuinely dangling link, nothing
            # to do with queueing. A real operator could never confirm one of these either (there
            # is no such barcode to scan), so letting one bad record's unrelated validation
            # failure abort the whole backfill - leaving even the healthy tasks unqueued - would
            # make this patch itself unreliable. Roll back just this one task's attempt (which
            # may include a Warehouse Order attach_task already created/incremented before the
            # save that actually failed) and keep going; see PLAN.md for what to do about the
            # dangling references themselves.
            frappe.db.rollback(save_point="cross_dock_backfill")
            failed.append(name)
            frappe.log_error(title="backfill_unqueued_cross_dock_tasks: could not attach", message=frappe.get_traceback())
    if failed:
        print(f"backfill_unqueued_cross_dock_tasks: {len(failed)} task(s) could not be attached (see Error Log): {failed}")

    # Every delivery with an open Cross Dock reservation has been sitting at picking_status
    # "Not Started" this whole time (that field only existed as Not Started/Partially Picked/
    # Picked until this same fix added "Not Relevant" - see _update_delivery_picking_status) -
    # recompute it now so the Outbound Monitor reflects reality immediately, not only for
    # deliveries touched by some future reservation/confirmation from here on.
    deliveries = frappe.get_all("Warehouse Request", filters={"request_type": "Cross Dock", "reference_doctype": "Outbound Delivery"},
        pluck="reference_name", distinct=True)
    for delivery_name in deliveries:
        _update_delivery_picking_status(delivery_name)
