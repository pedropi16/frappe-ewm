import frappe

from frappe_wms.services.warehouse_order import attach_task


def execute():
    # Every Putaway task created before the destination-bin-priority fix (see attach_task's own
    # comment in services/warehouse_order.py) landed with warehouse_order/queue left blank: the RF
    # app still lists these as "Open" work on the Putaway tasks screen (confirmed live in
    # production), but "Get next work" (pull_next_warehouse_order) can never actually serve them,
    # since that pull only ever looks at tasks already attached to a Warehouse Order/queue. A
    # non-terminal, docstatus<2 task with no warehouse_order is never a legitimate end state -
    # attach_task is safe to re-run on an already-inserted doc (it only sets fields and returns
    # early as a no-op if still no queue resolves), so simply retrying it now, with today's fixed
    # bin-priority logic, is enough to unstick them.
    orphans = frappe.get_all(
        "Warehouse Task",
        filters={"docstatus": ["<", 2], "status": "Open", "warehouse_order": ["in", ("", None)]},
        pluck="name",
    )
    for name in orphans:
        task = frappe.get_doc("Warehouse Task", name)
        attach_task(task, frappe.generate_hash(length=10), reference_doctype="Warehouse Request", reference_name=task.warehouse_request)
        if task.warehouse_order:
            task.save(ignore_permissions=True)
