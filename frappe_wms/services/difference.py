import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import OWNER_KEYS, dim_values, post_entries, transfer_stock
from frappe_wms.utils import require_role

# SAP EWM's Difference Analyzer, scoped to what this app's own Warehouse Task confirmation flow
# actually needs: an Over difference is real physical stock a resource found beyond a task's
# planned_quantity (confirm_task used to flatly reject this - round(new_confirmed,6) >
# round(planned_quantity,6) was always a ValidationError, with no way to record what was
# actually found); a Short difference is logged whenever raise_exception's revised_quantity
# closes a task below its ORIGINAL planned_quantity (previously silent - the shortfall just
# vanished into a lowered planned_quantity with no trace).

READ_ROLES = ("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
CLEAR_ROLES = ("WMS Inventory Controller", "WMS Supervisor")


def difference_bin_for_warehouse(warehouse):
    bin_name = frappe.db.get_value("WMS Warehouse", warehouse, "default_difference_bin")
    if not bin_name:
        frappe.throw(_("WMS Warehouse {0} has no Default Difference Bin configured - set one before an over-confirmation can be recorded").format(warehouse))
    return bin_name


def record_over_difference(task, excess_qty, idempotency_key):
    # The excess is real stock that must be accounted for right now - it lands in the
    # warehouse's Difference Bin as an Inventory Gain (701), pending a supervisor deciding
    # where it actually belongs (clear_over_difference).
    bin_name = difference_bin_for_warehouse(task.warehouse)
    stock_type = task.stock_type_to or task.stock_type_from
    entry = {
        "warehouse": task.warehouse, "product": task.product, "batch_no": task.batch_no, "serial_no": task.serial_no,
        "handling_unit": None, "storage_bin": bin_name, "stock_type": stock_type,
        "quantity": excess_qty, "stock_uom": task.stock_uom, "movement_type": "701", "reference_line": task.name,
    }
    if any(task.get(k) for k in OWNER_KEYS): entry.update(dim_values(task))  # the extra goods are the task's owner's
    post_entries([entry], task.doctype, task.name, idempotency_key)
    doc = frappe.get_doc({
        "doctype": "WMS Task Difference", "warehouse": task.warehouse, "warehouse_task": task.name, "task_type": task.task_type,
        "product": task.product, "stock_uom": task.stock_uom, "batch_no": task.batch_no, "serial_no": task.serial_no,
        "direction": "Over", "planned_quantity": task.planned_quantity, "difference_quantity": excess_qty,
        "stock_type": stock_type, "storage_bin": bin_name, "status": "Open",
    })
    doc.insert(ignore_permissions=True)
    from frappe_wms.services.erp_sync_queue import dispatch  # deferred: erpnext_sync imports a lot at module load
    dispatch("over_difference", doc)
    return doc.name


def record_short_difference(task, shortfall_qty, original_planned_quantity, exception_code=None, remarks=None):
    # Nothing to post - the shortfall never physically existed to move. Purely a logged record
    # for visibility, exactly like an Over difference's audit trail but with no stock movement:
    # the task's own planned_quantity has already been revised down by raise_exception, and any
    # real ledger reconciliation is left to a proper Physical Inventory Count, not guessed here.
    doc = frappe.get_doc({
        "doctype": "WMS Task Difference", "warehouse": task.warehouse, "warehouse_task": task.name, "task_type": task.task_type,
        "product": task.product, "stock_uom": task.stock_uom, "batch_no": task.batch_no, "serial_no": task.serial_no,
        "direction": "Short", "planned_quantity": original_planned_quantity, "difference_quantity": shortfall_qty,
        "stock_type": task.stock_type_from, "exception_code": exception_code, "clearance_remarks": remarks, "status": "Open",
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def list_open_differences(warehouse=None, direction=None):
    require_role(*READ_ROLES)
    filters = {"status": "Open"}
    if warehouse: filters["warehouse"] = warehouse
    if direction: filters["direction"] = direction
    return frappe.get_list("WMS Task Difference", filters=filters,
        fields=["name", "warehouse", "warehouse_task", "task_type", "product", "direction", "difference_quantity",
            "storage_bin", "exception_code", "creation"],
        order_by="creation asc", limit=100)


def clear_over_difference(name, destination_bin, destination_hu=None):
    require_role(*CLEAR_ROLES)
    doc = frappe.get_doc("WMS Task Difference", name, for_update=True)
    if doc.status != "Open": frappe.throw(_("Difference {0} is already cleared").format(name))
    if doc.direction != "Over": frappe.throw(_("Difference {0} is a Short difference - use clear_short_difference instead").format(name))
    source = {"warehouse": doc.warehouse, "product": doc.product, "batch_no": doc.batch_no, "serial_no": doc.serial_no,
        "handling_unit": None, "storage_bin": doc.storage_bin, "stock_type": doc.stock_type, "stock_uom": doc.stock_uom}
    destination = {"handling_unit": destination_hu, "storage_bin": destination_bin, "stock_type": doc.stock_type}
    transfer_stock(source=source, destination=destination, quantity=doc.difference_quantity, movement_type="301",
        reference_doctype=doc.doctype, reference_name=doc.name, idempotency_key=f"TDIFF-CLEAR:{doc.name}")
    doc.db_set({"status": "Cleared", "cleared_by": frappe.session.user, "cleared_at": now_datetime(), "cleared_to_bin": destination_bin})
    return {"difference": doc.name, "status": "Cleared"}


def clear_short_difference(name, remarks=None):
    require_role(*CLEAR_ROLES)
    doc = frappe.get_doc("WMS Task Difference", name, for_update=True)
    if doc.status != "Open": frappe.throw(_("Difference {0} is already cleared").format(name))
    if doc.direction != "Short": frappe.throw(_("Difference {0} is an Over difference - use clear_over_difference instead").format(name))
    updates = {"status": "Cleared", "cleared_by": frappe.session.user, "cleared_at": now_datetime()}
    if remarks: updates["clearance_remarks"] = remarks
    doc.db_set(updates)
    return {"difference": doc.name, "status": "Cleared"}
