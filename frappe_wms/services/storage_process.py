import frappe
from frappe_wms.services.determination import determine_destination_bin
from frappe_wms.services.warehouse_order import attach_task

STEP_FIELDS = ["name", "sequence", "step_code", "process_type", "source_storage_type",
    "destination_storage_type", "destination_bin", "require_hu_close",
    "create_task_automatically", "next_step_on_confirmation"]

def first_step(storage_process):
    rows = frappe.get_all("Storage Process Step", filters={"parent": storage_process},
        fields=STEP_FIELDS, order_by="sequence asc", limit=1)
    return rows[0] if rows else None

def _step_row(storage_process, step_code):
    rows = frappe.get_all("Storage Process Step", filters={"parent": storage_process, "step_code": step_code},
        fields=STEP_FIELDS, limit=1)
    return rows[0] if rows else None

def _next_step(storage_process, current_sequence):
    rows = frappe.get_all("Storage Process Step", filters={"parent": storage_process, "sequence": [">", current_sequence]},
        fields=STEP_FIELDS, order_by="sequence asc", limit=1)
    return rows[0] if rows else None

def build_task_for_step(request, step, predecessor):
    # Chains off the just-confirmed predecessor task: its destination is this step's source,
    # and whatever quantity actually arrived there (not the original request quantity, which
    # may not match after a partial/revised confirmation) is what moves next.
    process_type = frappe.get_cached_doc("Warehouse Process Type", step.process_type)
    source_bin = predecessor.destination_bin
    source_hu = predecessor.destination_hu or predecessor.source_hu
    hu_type = frappe.db.get_value("Handling Unit", source_hu, "hu_type") if source_hu else None
    source_storage_type = frappe.db.get_value("Storage Bin", source_bin, "storage_type") if source_bin else step.source_storage_type
    destination_bin = step.destination_bin
    if not destination_bin and process_type.destination_required:
        # A step's destination_storage_type is informational only - honoring it as a hard
        # override would mean bypassing Bin Determination Rule matching entirely. Instead,
        # same as every other activity, the warehouse configures a Bin Determination Rule for
        # this step's activity; step.destination_bin remains available as a fixed override
        # when a rule isn't wanted.
        destination_bin = determine_destination_bin({
            "warehouse": request.warehouse, "activity": process_type.activity, "item": request.product,
            "stock_type": request.stock_type, "hu_type": hu_type, "source_storage_type": source_storage_type,
            "destination_hu": source_hu,
        })
    task = frappe.get_doc({
        "doctype": "Warehouse Task", "warehouse_request": request.name, "task_type": process_type.activity,
        "warehouse": request.warehouse, "product": request.product, "planned_quantity": predecessor.confirmed_quantity,
        "stock_uom": request.stock_uom, "batch_no": predecessor.batch_no, "serial_no": predecessor.serial_no,
        "source_bin": source_bin, "source_hu": source_hu, "destination_bin": destination_bin,
        "stock_type_from": request.stock_type, "stock_type_to": request.stock_type,
        "movement_type": process_type.movement_type, "priority": request.priority or "Normal", "status": "Open",
        "predecessor_task": predecessor.name,
    })
    attach_task(task, frappe.generate_hash(length=10), reference_doctype="Warehouse Request", reference_name=request.name)
    task.insert(ignore_permissions=True)
    return task

def advance_to_next_step(task):
    # Called right after a task that's part of a Storage Process fully confirms. Best-effort:
    # any missing piece (no request, no process, no next step, or the next step isn't marked
    # for automatic creation) just ends the chain instead of raising - a chain stopping at a
    # manual step is expected behavior, not an error.
    if not task.warehouse_request: return None
    request = frappe.get_doc("Warehouse Request", task.warehouse_request)
    if not request.storage_process or not request.process_step: return None
    current = _step_row(request.storage_process, request.process_step)
    if not current or not current.next_step_on_confirmation: return None
    next_step = _next_step(request.storage_process, current.sequence)
    if not next_step or not next_step.create_task_automatically: return None
    new_task = build_task_for_step(request, next_step, task)
    frappe.db.set_value("Warehouse Request", request.name, "process_step", next_step.step_code)
    return new_task.name
