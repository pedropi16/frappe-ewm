import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import transfer_stock, release_allocation
from frappe_wms.services.determination import determine_destination_bin
from frappe_wms.utils import require_role

TASK_TYPE_BY_REQUEST = {
    "Unload": "Unload", "Putaway": "Putaway", "Pick": "Pick", "Replenish": "Putaway",
    "Internal Move": "Internal Move", "Stage": "Stage", "Load": "Load",
    "Unload Vehicle": "Unload", "Posting Change": "Posting Change", "Inventory Count": "Inventory Count",
}

def create_tasks_for_request(request_name):
    frappe.db.sql("select name from `tabWarehouse Request` where name=%s for update", request_name)
    request = frappe.get_doc("Warehouse Request", request_name)
    if request.status not in {"Draft", "Open", "Partially Tasked"}: frappe.throw(_("Warehouse Request is not open for tasking"))
    remaining = flt(request.requested_quantity) - flt(request.created_quantity)
    if remaining <= 0: frappe.throw(_("Warehouse Request is already fully tasked"))
    task_type = TASK_TYPE_BY_REQUEST.get(request.request_type)
    if not task_type: frappe.throw(_("No warehouse task type mapped for request type {0}").format(request.request_type))
    process_type = frappe.get_cached_doc("Warehouse Process Type", request.process_type) if request.process_type else None
    movement_type = process_type.movement_type if process_type else None
    if not movement_type: frappe.throw(_("Warehouse Process Type must define a movement type before tasking"))
    destination_bin = request.destination_bin
    if not destination_bin and process_type and process_type.destination_required:
        hu_type = frappe.db.get_value("Handling Unit", request.source_hu, "hu_type") if request.source_hu else None
        source_storage_type = frappe.db.get_value("Storage Bin", request.source_bin, "storage_type") if request.source_bin else None
        destination_bin = determine_destination_bin({"warehouse": request.warehouse, "activity": process_type.activity, "item": request.product, "stock_type": request.stock_type, "hu_type": hu_type, "source_storage_type": source_storage_type})
    task = frappe.get_doc({"doctype": "Warehouse Task", "warehouse_request": request.name, "task_type": task_type, "warehouse": request.warehouse, "product": request.product, "planned_quantity": remaining, "stock_uom": request.stock_uom, "source_bin": request.source_bin, "destination_bin": destination_bin, "source_hu": request.source_hu, "destination_hu": request.destination_hu, "stock_type_from": request.stock_type, "stock_type_to": request.stock_type, "movement_type": movement_type, "priority": request.priority or "Normal", "status": "Open", "idempotency_key": f"WT:{request.name}"})
    task.insert(ignore_permissions=True)
    frappe.db.set_value("Warehouse Request", request.name, {"created_quantity": flt(request.created_quantity) + remaining, "status": "Fully Tasked"})
    return task.name

def create_pick_tasks(delivery_name):
    delivery = frappe.get_doc("Outbound Delivery", delivery_name)
    if not delivery.staging_bin: frappe.throw(_("Outbound Delivery must have a staging bin before picking tasks can be created"))
    process_type = frappe.get_cached_doc("Warehouse Process Type", "OB_PICK")
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": delivery_name, "status": "Allocated"}, fields=["*"])
    if not allocations: frappe.throw(_("No open allocations to create pick tasks for"))
    created = []
    for allocation in allocations:
        stock_uom = frappe.db.get_value("WMS Stock Balance", allocation.stock_balance, "stock_uom")
        task = frappe.get_doc({"doctype": "Warehouse Task", "stock_allocation": allocation.name, "task_type": "Pick", "warehouse": delivery.warehouse, "product": allocation.product, "planned_quantity": allocation.allocated_quantity, "stock_uom": stock_uom, "batch_no": allocation.batch_no, "serial_no": allocation.serial_no, "source_bin": allocation.storage_bin, "destination_bin": delivery.staging_bin, "source_hu": allocation.handling_unit, "stock_type_from": allocation.stock_type, "stock_type_to": allocation.stock_type, "movement_type": process_type.movement_type, "priority": delivery.priority or "Normal", "status": "Open", "idempotency_key": f"WT:{allocation.name}"})
        task.insert(ignore_permissions=True)
        frappe.db.set_value("Stock Allocation", allocation.name, "status", "Released")
        created.append(task.name)
    delivery.db_set("status", "Picking")
    return created

def confirm_task(task_name, scanned_source=None, scanned_destination=None, confirmed_quantity=None, destination_hu=None, device=None, idempotency_key=None):
    require_role("WMS Operator", "WMS Supervisor")
    frappe.db.sql("select name from `tabWarehouse Task` where name=%s for update", task_name)
    task = frappe.get_doc("Warehouse Task", task_name)
    if task.status == "Confirmed": return {"task": task.name, "status": task.status, "already_confirmed": True}
    if task.docstatus == 2 or task.status in {"Cancelled", "Exception"}: frappe.throw(_("Task is not confirmable"))
    if scanned_source and scanned_source not in {task.source_bin, task.source_hu}: frappe.throw(_("Scanned source does not match the task"))
    if scanned_destination and scanned_destination not in {task.destination_bin, task.destination_hu}: frappe.throw(_("Scanned destination does not match the task"))
    already_confirmed = flt(task.confirmed_quantity)
    qty = flt(confirmed_quantity) if confirmed_quantity is not None else flt(task.planned_quantity) - already_confirmed
    new_confirmed = already_confirmed + qty
    if qty <= 0 or round(new_confirmed, 6) > round(flt(task.planned_quantity), 6): frappe.throw(_("Invalid confirmed quantity"))
    source = {"warehouse": task.warehouse, "product": task.product, "batch_no": task.batch_no, "serial_no": task.serial_no, "handling_unit": task.source_hu, "storage_bin": task.source_bin, "stock_type": task.stock_type_from, "stock_uom": task.stock_uom}
    destination = {"handling_unit": destination_hu or task.destination_hu or task.source_hu, "storage_bin": task.destination_bin, "stock_type": task.stock_type_to or task.stock_type_from}
    key = idempotency_key or f"{task.idempotency_key or task.name}:{already_confirmed}"
    transfer_stock(source=source, destination=destination, quantity=qty, movement_type=task.movement_type, reference_doctype=task.doctype, reference_name=task.name, idempotency_key=key, warehouse_task=task.name, device=device)
    fully_confirmed = round(new_confirmed, 6) >= round(flt(task.planned_quantity), 6)
    status = "Confirmed" if fully_confirmed else "Partially Confirmed"
    updates = {"confirmed_quantity": new_confirmed, "status": status, "confirmed_at": now_datetime(), "confirmed_by": frappe.session.user, "confirmation_device": device, "idempotency_key": key}
    if fully_confirmed: updates["docstatus"] = 1
    task.db_set(updates, update_modified=True)
    _update_request(task.warehouse_request)
    _update_allocation(task, qty)
    if fully_confirmed: _move_hu_if_complete(task, destination_hu)
    return {"task": task.name, "status": status, "quantity": qty}

def _update_allocation(task, qty):
    if not task.stock_allocation: return
    allocation = frappe.get_doc("Stock Allocation", task.stock_allocation)
    picked = flt(allocation.picked_quantity) + qty
    status = "Picked" if picked >= flt(allocation.allocated_quantity) else "Partially Picked"
    frappe.db.set_value("Stock Allocation", allocation.name, {"picked_quantity": picked, "status": status})
    release_allocation({"warehouse": task.warehouse, "product": task.product, "batch_no": task.batch_no, "serial_no": task.serial_no, "handling_unit": task.source_hu, "storage_bin": task.source_bin, "stock_type": task.stock_type_from}, qty)
    if allocation.outbound_delivery_item:
        current = flt(frappe.db.get_value("Outbound Delivery Item", allocation.outbound_delivery_item, "picked_quantity"))
        frappe.db.set_value("Outbound Delivery Item", allocation.outbound_delivery_item, "picked_quantity", current + qty)
    _update_delivery_picking_status(allocation.outbound_delivery)

def _update_delivery_picking_status(delivery_name):
    if not delivery_name: return
    rows = frappe.get_all("Outbound Delivery Item", filters={"parent": delivery_name}, fields=["requested_quantity", "picked_quantity"])
    if not rows: return
    fully_picked = all(flt(r.picked_quantity) >= flt(r.requested_quantity) for r in rows)
    any_picked = any(flt(r.picked_quantity) > 0 for r in rows)
    picking_status = "Picked" if fully_picked else ("Partially Picked" if any_picked else "Not Started")
    values = {"picking_status": picking_status}
    if fully_picked: values["status"] = "Picked"
    frappe.db.set_value("Outbound Delivery", delivery_name, values)

def _update_request(name):
    if not name: return
    totals = frappe.db.sql("select coalesce(sum(planned_quantity),0), coalesce(sum(confirmed_quantity),0), count(*), sum(status='Confirmed') from `tabWarehouse Task` where warehouse_request=%s and docstatus<2", name)[0]
    status = "Completed" if totals[2] and totals[2] == totals[3] else "In Process"
    frappe.db.set_value("Warehouse Request", name, {"created_quantity": totals[0], "confirmed_quantity": totals[1], "status": status})

def _move_hu_if_complete(task, destination_hu=None):
    hu = destination_hu or task.destination_hu or task.source_hu
    if not hu or not task.destination_bin: return
    if task.move_top_hu:
        hu = frappe.db.get_value("Handling Unit", hu, "top_hu") or hu
    _move_hu_and_descendants(hu, task.destination_bin, task.source_bin, task, top_level=True)

def _move_hu_and_descendants(hu, destination_bin, source_bin, task, top_level):
    doc = frappe.get_doc("Handling Unit", hu)
    if doc.current_bin == destination_bin: return
    bin_before = doc.current_bin
    doc.flags.wms_service_update = True
    doc.current_bin = destination_bin
    if top_level: doc.status = "Staged" if task.task_type == "Stage" else doc.status
    doc.save(ignore_permissions=True)
    frappe.get_doc({"doctype": "Handling Unit Event", "handling_unit": hu, "event_type": "Moved", "bin_before": bin_before or source_bin, "bin_after": destination_bin, "warehouse_task": task.name, "event_timestamp": now_datetime(), "performed_by": frappe.session.user}).insert(ignore_permissions=True)
    for child in frappe.get_all("Handling Unit", filters={"parent_hu": hu}, pluck="name"):
        _move_hu_and_descendants(child, destination_bin, source_bin, task, top_level=False)
