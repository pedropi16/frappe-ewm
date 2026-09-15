import uuid
import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import transfer_stock
from frappe_wms.utils import require_role

def confirm_task(task_name, scanned_source=None, scanned_destination=None, confirmed_quantity=None, destination_hu=None, device=None, idempotency_key=None):
    require_role("WMS Operator", "WMS Supervisor")
    frappe.db.sql("select name from `tabWarehouse Task` where name=%s for update", task_name)
    task = frappe.get_doc("Warehouse Task", task_name)
    if task.status == "Confirmed": return {"task": task.name, "status": task.status, "already_confirmed": True}
    if task.docstatus == 2 or task.status in {"Cancelled", "Exception"}: frappe.throw(_("Task is not confirmable"))
    if scanned_source and scanned_source not in {task.source_bin, task.source_hu}: frappe.throw(_("Scanned source does not match the task"))
    if scanned_destination and scanned_destination not in {task.destination_bin, task.destination_hu}: frappe.throw(_("Scanned destination does not match the task"))
    qty = flt(confirmed_quantity or task.planned_quantity)
    if qty <= 0 or qty > flt(task.planned_quantity): frappe.throw(_("Invalid confirmed quantity"))
    source = {"warehouse": task.warehouse, "product": task.product, "batch_no": task.batch_no, "serial_no": task.serial_no, "handling_unit": task.source_hu, "storage_bin": task.source_bin, "stock_type": task.stock_type_from, "stock_uom": task.stock_uom}
    destination = {"handling_unit": destination_hu or task.destination_hu or task.source_hu, "storage_bin": task.destination_bin, "stock_type": task.stock_type_to or task.stock_type_from}
    key = idempotency_key or task.idempotency_key or str(uuid.uuid4())
    transfer_stock(source=source, destination=destination, quantity=qty, movement_type=task.movement_type, reference_doctype=task.doctype, reference_name=task.name, idempotency_key=key, warehouse_task=task.name, device=device)
    task.db_set({"confirmed_quantity": qty, "status": "Confirmed", "confirmed_at": now_datetime(), "confirmed_by": frappe.session.user, "confirmation_device": device, "idempotency_key": key, "docstatus": 1}, update_modified=True)
    _update_request(task.warehouse_request)
    _move_hu_if_complete(task, destination_hu)
    return {"task": task.name, "status": "Confirmed", "quantity": qty}

def _update_request(name):
    if not name: return
    totals = frappe.db.sql("select coalesce(sum(planned_quantity),0), coalesce(sum(confirmed_quantity),0), count(*), sum(status='Confirmed') from `tabWarehouse Task` where warehouse_request=%s and docstatus<2", name)[0]
    status = "Completed" if totals[2] and totals[2] == totals[3] else "In Process"
    frappe.db.set_value("Warehouse Request", name, {"created_quantity": totals[0], "confirmed_quantity": totals[1], "status": status})

def _move_hu_if_complete(task, destination_hu=None):
    hu = destination_hu or task.destination_hu or task.source_hu
    if not hu or not task.destination_bin: return
    doc = frappe.get_doc("Handling Unit", hu)
    doc.flags.wms_service_update = True
    doc.current_bin = task.destination_bin
    doc.status = "Staged" if task.task_type == "Stage" else doc.status
    doc.save(ignore_permissions=True)
    frappe.get_doc({"doctype":"Handling Unit Event", "handling_unit":hu, "event_type":"Moved", "bin_before":task.source_bin, "bin_after":task.destination_bin, "warehouse_task":task.name, "event_timestamp":now_datetime(), "performed_by":frappe.session.user}).insert(ignore_permissions=True)
