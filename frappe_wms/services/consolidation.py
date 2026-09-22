import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.determination import determine_process_type
from frappe_wms.services.warehouse_order import attach_task
from frappe_wms.services.handling_unit import get_or_create_handling_unit
from frappe_wms.services.deconsolidation import create_deconsolidation_tasks
from frappe_wms.utils import require_role

# Gather-then-split: an operator combines demand from several Outbound Deliveries and/or Work
# Orders onto one physical HU at a shared staging point (e.g. a Production Supply Area bin),
# then splits it apart later via the existing, unmodified Deconsolidation mechanism. Picking
# and PSA replenishment run to completion exactly as today - nothing here changes where their
# own tasks land; a Consolidation task is a second, separate hop moving already-confirmed
# stock from wherever it normally ends up onto the shared target HU.

LINE_DONE_STATUSES = ("Gathered", "Deconsolidated")


def _already_joined(reference_doctype, reference_name):
    return bool(frappe.db.exists("Consolidation Group Line", {
        "reference_doctype": reference_doctype, "reference_name": reference_name, "status": ["!=", "Cancelled"],
    }))


def create_consolidation_group(warehouse, staging_bin, priority=None, target_hu=None):
    require_role("WMS Operator", "WMS Supervisor")
    group = frappe.get_doc({
        "doctype": "Consolidation Group", "warehouse": warehouse, "staging_bin": staging_bin,
        "priority": priority or "Normal", "status": "Draft", "gather_status": "Not Started",
    })
    group.insert(ignore_permissions=True)
    if target_hu:
        set_consolidation_target_hu(group.name, target_hu)
    return group.name


def set_consolidation_target_hu(group_name, target_hu=None, hu_type=None):
    require_role("WMS Operator", "WMS Supervisor")
    group = frappe.get_doc("Consolidation Group", group_name)
    hu = get_or_create_handling_unit(target_hu, hu_type, group.staging_bin, group.warehouse)
    group.target_hu = hu
    group.save(ignore_permissions=True)
    return hu


def find_joinable_references(barcode):
    # A scanned barcode could be an Outbound Delivery, a Work Order, or the reference row
    # itself (Stock Allocation / Warehouse Request name) - resolve whichever it is into the
    # candidate lines an operator could add, already excluding anything joined elsewhere.
    barcode = (barcode or "").strip()
    if not barcode:
        return []
    candidates = []

    delivery = barcode if frappe.db.exists("Outbound Delivery", barcode) else frappe.db.get_value("Outbound Delivery", {"outbound_delivery_number": barcode}, "name")
    if delivery:
        for a in frappe.get_all("Stock Allocation", filters={"outbound_delivery": delivery, "status": "Picked"}, fields=["name", "product", "allocated_quantity", "picked_quantity"]):
            if _already_joined("Stock Allocation", a.name): continue
            candidates.append({
                "reference_doctype": "Stock Allocation", "reference_name": a.name, "product": a.product,
                "quantity": flt(a.picked_quantity) or flt(a.allocated_quantity), "source_label": delivery,
            })

    if frappe.db.exists("Work Order", barcode):
        for r in frappe.get_all("Warehouse Request", filters={"reference_doctype": "Work Order", "reference_name": barcode, "status": "Completed"}, fields=["name", "product", "confirmed_quantity"]):
            if _already_joined("Warehouse Request", r.name): continue
            candidates.append({
                "reference_doctype": "Warehouse Request", "reference_name": r.name, "product": r.product,
                "quantity": flt(r.confirmed_quantity), "source_label": barcode,
            })

    if not candidates:
        allocation = frappe.db.get_value("Stock Allocation", barcode, ["product", "allocated_quantity", "picked_quantity", "status", "outbound_delivery"], as_dict=True)
        if allocation and allocation.status == "Picked" and not _already_joined("Stock Allocation", barcode):
            candidates.append({
                "reference_doctype": "Stock Allocation", "reference_name": barcode, "product": allocation.product,
                "quantity": flt(allocation.picked_quantity) or flt(allocation.allocated_quantity), "source_label": allocation.outbound_delivery,
            })
        request = frappe.db.get_value("Warehouse Request", barcode, ["product", "confirmed_quantity", "status", "reference_name"], as_dict=True)
        if request and request.status == "Completed" and not _already_joined("Warehouse Request", barcode):
            candidates.append({
                "reference_doctype": "Warehouse Request", "reference_name": barcode, "product": request.product,
                "quantity": flt(request.confirmed_quantity), "source_label": request.reference_name,
            })
    return candidates


def add_consolidation_line(group_name, reference_doctype, reference_name):
    require_role("WMS Operator", "WMS Supervisor")
    if reference_doctype not in ("Stock Allocation", "Warehouse Request"):
        frappe.throw(_("Consolidation lines can only reference a Stock Allocation or a Warehouse Request"))
    if _already_joined(reference_doctype, reference_name):
        frappe.throw(_("{0} {1} is already part of a Consolidation Group").format(reference_doctype, reference_name))
    group = frappe.get_doc("Consolidation Group", group_name)
    if group.status not in ("Draft", "Open"):
        frappe.throw(_("Consolidation Group is not open"))

    if reference_doctype == "Stock Allocation":
        allocation = frappe.get_doc("Stock Allocation", reference_name)
        if allocation.status != "Picked":
            frappe.throw(_("Stock Allocation {0} has not been fully picked yet").format(reference_name))
        delivery_warehouse = frappe.db.get_value("Outbound Delivery", allocation.outbound_delivery, "warehouse")
        if delivery_warehouse != group.warehouse:
            frappe.throw(_("Stock Allocation {0} belongs to a different warehouse than this Consolidation Group").format(reference_name))
        destination_bin = frappe.db.get_value("Outbound Delivery", allocation.outbound_delivery, "staging_bin")
        if not destination_bin:
            frappe.throw(_("Outbound Delivery {0} has no staging bin").format(allocation.outbound_delivery))
        line = {
            "reference_doctype": reference_doctype, "reference_name": reference_name, "product": allocation.product,
            "batch_no": allocation.batch_no, "serial_no": allocation.serial_no, "stock_type": allocation.stock_type,
            "stock_uom": frappe.db.get_value("WMS Stock Balance", allocation.stock_balance, "stock_uom"),
            "quantity": flt(allocation.picked_quantity) or flt(allocation.allocated_quantity),
            "final_destination_bin": destination_bin, "status": "Ready",
        }
    else:
        request = frappe.get_doc("Warehouse Request", reference_name)
        if request.status != "Completed":
            frappe.throw(_("Warehouse Request {0} has not been fully confirmed yet - the Work Order line may still be awaiting staging, or has no matching request at all").format(reference_name))
        if request.warehouse != group.warehouse:
            frappe.throw(_("Warehouse Request {0} belongs to a different warehouse than this Consolidation Group").format(reference_name))
        if not request.destination_bin:
            frappe.throw(_("Warehouse Request {0} has no destination bin").format(reference_name))
        line = {
            "reference_doctype": reference_doctype, "reference_name": reference_name, "product": request.product,
            "stock_type": request.stock_type, "stock_uom": request.stock_uom, "quantity": flt(request.confirmed_quantity),
            "final_destination_bin": request.destination_bin, "status": "Ready",
        }

    group.append("lines", line)
    group.status = "Open"
    group.save(ignore_permissions=True)
    return group.lines[-1].name


def remove_consolidation_line(group_name, line_name):
    require_role("WMS Operator", "WMS Supervisor")
    group = frappe.get_doc("Consolidation Group", group_name)
    line = next((l for l in group.lines if l.name == line_name), None)
    if not line:
        frappe.throw(_("Line not found on this Consolidation Group"))
    if line.status in LINE_DONE_STATUSES:
        frappe.throw(_("Cannot remove a line whose stock is already on the target Handling Unit"))
    group.lines = [l for l in group.lines if l.name != line_name]
    group.save(ignore_permissions=True)


def create_consolidation_tasks(destination_hu, destination_bin, warehouse, lines):
    # The mirror of create_deconsolidation_tasks, run in reverse: several varying sources onto
    # one fixed destination instead of one fixed source onto several destinations. Same shape
    # (one task per line, shared batch_key, no zero-sum posting check since this isn't a
    # balanced transfer pair - each line is its own single-entry post via confirm_task later).
    require_role("WMS Operator", "WMS Supervisor")
    if not lines:
        frappe.throw(_("Nothing to gather"))
    process_type_name = determine_process_type(warehouse, "Consolidation", item=lines[0].get("product"), stock_type=lines[0].get("stock_type"), default="CONSOL")
    process_type = frappe.get_cached_doc("Warehouse Process Type", process_type_name)
    batch_key = frappe.generate_hash(length=10)
    created = []
    for line in lines:
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Consolidation", "warehouse": warehouse,
            "product": line["product"], "planned_quantity": line["quantity"], "stock_uom": line.get("stock_uom"),
            "batch_no": line.get("batch_no"), "serial_no": line.get("serial_no"),
            "source_bin": line["source_bin"], "source_hu": line.get("source_hu"),
            "destination_bin": destination_bin, "destination_hu": destination_hu,
            "stock_type_from": line["stock_type"], "stock_type_to": line["stock_type"],
            "movement_type": process_type.movement_type, "priority": "Normal", "status": "Open",
            "consolidation_group_line": line.get("consolidation_group_line"),
        })
        attach_task(task, batch_key)
        task.insert(ignore_permissions=True)
        created.append(task.name)
    return created


def gather_consolidation_group(group_name):
    require_role("WMS Operator", "WMS Supervisor")
    group = frappe.get_doc("Consolidation Group", group_name)
    if group.status not in ("Draft", "Open"):
        frappe.throw(_("Consolidation Group is not open"))
    if not group.target_hu:
        frappe.throw(_("Set a target Handling Unit before gathering"))
    ready = [l for l in group.lines if l.status == "Ready"]
    if not ready:
        frappe.throw(_("No lines are ready to gather"))

    line_rows, task_lines = [], []
    for l in ready:
        # Re-resolved against live data, not what was snapshotted when the line was added - a
        # pick denial (revised quantity) or a partial replenishment confirmation between then
        # and now means the real available amount can differ from the original plan.
        if l.reference_doctype == "Stock Allocation":
            allocation = frappe.get_doc("Stock Allocation", l.reference_name)
            qty = flt(allocation.picked_quantity) or flt(allocation.allocated_quantity)
            # allocation.storage_bin is where the stock was BEFORE picking - once Picked, the
            # Pick task has already moved it (same physical HU, relocated) to the delivery's
            # own staging bin, which is where it actually sits now.
            source_bin = frappe.db.get_value("Outbound Delivery", allocation.outbound_delivery, "staging_bin")
            source_hu = allocation.handling_unit
        else:
            request = frappe.get_doc("Warehouse Request", l.reference_name)
            qty = flt(request.confirmed_quantity)
            # destination_hu is never set for a production-supply request (create_tasks_for_
            # request only resolves one when destination_bin is absent, and this call always
            # supplies it) - the physical HU carried unchanged through the Putaway confirm is
            # request.source_hu (confirm_task falls back to task.source_hu when no destination
            # HU is given), not destination_hu.
            source_bin, source_hu = request.destination_bin, request.source_hu
        if qty <= 0:
            continue
        l.quantity = qty
        line_rows.append(l)
        task_lines.append({
            "consolidation_group_line": l.name, "product": l.product, "batch_no": l.batch_no, "serial_no": l.serial_no,
            "stock_type": l.stock_type, "stock_uom": l.stock_uom, "quantity": qty,
            "source_bin": source_bin, "source_hu": source_hu,
        })
    if not task_lines:
        frappe.throw(_("Nothing currently available to gather"))

    created = create_consolidation_tasks(group.target_hu, group.staging_bin, group.warehouse, task_lines)
    for l, task_name in zip(line_rows, created):
        l.gather_task = task_name
    group.gather_status = "Partially Gathered"
    group.save(ignore_permissions=True)
    return created


def complete_consolidation_group(group_name):
    require_role("WMS Operator", "WMS Supervisor")
    group = frappe.get_doc("Consolidation Group", group_name)
    if group.gather_status != "Fully Gathered":
        frappe.throw(_("All lines must be gathered onto the target Handling Unit before splitting to destinations"))
    lines = [l for l in group.lines if l.status == "Gathered"]
    if not lines:
        frappe.throw(_("Nothing to split to destinations"))
    decon_lines = [{
        "product": l.product, "batch_no": l.batch_no, "serial_no": l.serial_no, "stock_type": l.stock_type,
        "quantity": l.quantity, "destination_bin": l.final_destination_bin, "destination_hu": l.final_destination_hu,
        "consolidation_group_line": l.name,
    } for l in lines]
    created = create_deconsolidation_tasks(group.target_hu, decon_lines)
    for l, task_name in zip(lines, created):
        l.deconsolidation_task = task_name
    group.save(ignore_permissions=True)
    return created


def update_consolidation_progress(task, qty):
    if not task.consolidation_group_line:
        return
    group_name = frappe.db.get_value("Consolidation Group Line", task.consolidation_group_line, "parent")
    if not group_name:
        return
    frappe.db.sql("select name from `tabConsolidation Group` where name=%s for update", group_name)
    group = frappe.get_doc("Consolidation Group", group_name)
    line = next((l for l in group.lines if l.name == task.consolidation_group_line), None)
    if not line:
        return
    if task.task_type == "Consolidation":
        line.gathered_quantity = flt(line.gathered_quantity) + flt(qty)
        if round(line.gathered_quantity, 6) >= round(flt(line.quantity), 6):
            line.status = "Gathered"
    elif task.task_type == "Deconsolidation":
        line.fulfilled_quantity = flt(line.fulfilled_quantity) + flt(qty)
        if round(line.fulfilled_quantity, 6) >= round(flt(line.quantity), 6):
            line.status = "Deconsolidated"

    statuses = [l.status for l in group.lines]
    if statuses and all(s in LINE_DONE_STATUSES for s in statuses):
        group.gather_status = "Fully Gathered"
    elif any(s in LINE_DONE_STATUSES for s in statuses):
        group.gather_status = "Partially Gathered"
    if statuses and all(s == "Deconsolidated" for s in statuses):
        group.status = "Completed"
        group.completed_at = now_datetime()
        group.completed_by = frappe.session.user
    group.save(ignore_permissions=True)


def cancel_consolidation_lines_for_allocations(allocation_names):
    # Called from cancel_allocations_for_delivery: a Consolidation Group Line pointing at a
    # cancelled allocation would otherwise sit there referencing dead demand forever.
    if not allocation_names:
        return
    lines = frappe.get_all("Consolidation Group Line", filters={
        "reference_doctype": "Stock Allocation", "reference_name": ["in", allocation_names], "status": ["!=", "Cancelled"],
    }, fields=["name", "parent"])
    for group_name in {l.parent for l in lines}:
        group = frappe.get_doc("Consolidation Group", group_name)
        touched = False
        for l in group.lines:
            if l.reference_doctype == "Stock Allocation" and l.reference_name in allocation_names and l.status not in LINE_DONE_STATUSES:
                l.status = "Cancelled"
                touched = True
        if touched:
            group.save(ignore_permissions=True)
