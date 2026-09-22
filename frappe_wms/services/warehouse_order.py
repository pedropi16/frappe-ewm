import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.utils import require_role

RESOURCE_ROLES = ("WMS Operator", "WMS Receiver", "WMS Picker", "WMS Packer", "WMS Loader", "WMS Supervisor")
OPEN_WO_STATUSES = ("Open", "Assigned", "In Process")
PRIORITY_RANK = {"Urgent": 0, "High": 1, "Normal": 2, "Low": 3}

def _by_priority_then_age(rows):
    return sorted(rows, key=lambda r: (PRIORITY_RANK.get(r.priority, 2), r.creation))

def determine_queue(warehouse, activity, storage_type=None):
    filters = {"warehouse": warehouse, "activity": activity, "active": 1}
    if storage_type:
        queue = frappe.db.get_value("Warehouse Queue", {**filters, "storage_type": storage_type}, "name")
        if queue: return queue
    return frappe.db.get_value("Warehouse Queue", {**filters, "storage_type": ["in", ["", None]]}, "name")

def _resource_for_queue(queue):
    # Least-loaded active resource currently logged onto this queue (round-robins naturally
    # as each new Warehouse Order goes to whoever has fewest open ones).
    resources = frappe.get_all("WMS Resource", filters={"current_queue": queue, "active": 1}, pluck="name")
    if not resources: return None
    counts = dict(frappe.db.sql(
        "select assigned_resource, count(*) from `tabWarehouse Order` "
        "where queue=%s and status in %s and assigned_resource is not null group by assigned_resource",
        (queue, OPEN_WO_STATUSES),
    ))
    return min(resources, key=lambda r: counts.get(r, 0))

def _matching_wo_creation_rule(warehouse, activity, item_group=None, stock_type=None):
    for rule in frappe.get_all("WO Creation Rule", filters={"warehouse": warehouse, "activity": activity, "active": 1},
            fields=["name", "item_group", "stock_type", "maximum_tasks"], order_by="priority asc"):
        if rule.item_group and rule.item_group != item_group: continue
        if rule.stock_type and rule.stock_type != stock_type: continue
        return rule
    return None

def _batch_key_with_room(queue, batch_key, maximum_tasks):
    # A WO Creation Rule caps how many tasks one Warehouse Order can hold. batch_key alone
    # normally guarantees reuse of the same WO; once a rule caps it, later tasks spill into
    # a fresh WO under a derived batch_key instead of piling onto a full one.
    suffix = 0
    while True:
        candidate = batch_key if suffix == 0 else f"{batch_key}#{suffix}"
        existing = frappe.db.get_value("Warehouse Order", {"queue": queue, "batch_key": candidate, "status": ["in", OPEN_WO_STATUSES]}, ["task_count"], as_dict=True)
        if not existing or (existing.task_count or 0) < maximum_tasks:
            return candidate
        suffix += 1

def get_or_create_warehouse_order(warehouse, activity, queue, batch_key, priority="Normal", wave=None, reference_doctype=None, reference_name=None, item_group=None, stock_type=None):
    rule = _matching_wo_creation_rule(warehouse, activity, item_group, stock_type)
    if rule and rule.maximum_tasks:
        batch_key = _batch_key_with_room(queue, batch_key, rule.maximum_tasks)
    existing = frappe.db.get_value("Warehouse Order", {"queue": queue, "batch_key": batch_key, "status": ["in", OPEN_WO_STATUSES]}, "name")
    if existing: return existing
    resource = _resource_for_queue(queue) if queue else None
    wo = frappe.get_doc({
        "doctype": "Warehouse Order", "warehouse": warehouse, "activity": activity, "queue": queue,
        "batch_key": batch_key, "wave": wave, "priority": priority,
        "assigned_resource": resource, "status": "Assigned" if resource else "Open",
        "reference_doctype": reference_doctype, "reference_name": reference_name,
    })
    wo.insert(ignore_permissions=True)
    return wo.name

def attach_task(task_doc, batch_key, reference_doctype=None, reference_name=None):
    # Called on an unsaved Warehouse Task before insert; sets warehouse_order/queue/assigned_resource
    # in place. No-op (task stays unqueued, back-compat) if no queue is configured for this activity.
    storage_type = None
    for bin_field in ("source_bin", "destination_bin"):
        bin_name = task_doc.get(bin_field)
        if bin_name:
            storage_type = frappe.db.get_value("Storage Bin", bin_name, "storage_type")
            if storage_type: break
    queue = determine_queue(task_doc.warehouse, task_doc.task_type, storage_type)
    if not queue: return
    item_group = frappe.db.get_value("Item", task_doc.product, "item_group") if task_doc.product else None
    wo_name = get_or_create_warehouse_order(
        task_doc.warehouse, task_doc.task_type, queue, batch_key,
        priority=task_doc.priority or "Normal", wave=task_doc.get("wave"),
        reference_doctype=reference_doctype, reference_name=reference_name,
        item_group=item_group, stock_type=task_doc.get("stock_type_from"),
    )
    wo = frappe.get_cached_doc("Warehouse Order", wo_name)
    task_doc.warehouse_order = wo_name
    task_doc.queue = queue
    task_doc.assigned_resource = wo.assigned_resource
    if wo.assigned_resource: task_doc.status = "Assigned"
    task_count = frappe.db.get_value("Warehouse Order", wo_name, "task_count") or 0
    frappe.db.set_value("Warehouse Order", wo_name, "task_count", task_count + 1)
    _gate_on_sequence(task_doc, wo_name, task_count)

NON_TERMINAL_STATUSES = ("Open", "On Hold", "Available", "Assigned", "In Process", "Partially Confirmed", "Exception")

def _gate_on_sequence(task_doc, wo_name, arrival_index):
    # SAP EWM-style strict sequence: within one Warehouse Order, only the lowest-sequence
    # not-yet-confirmed task is workable - everything behind it sits On Hold until its turn.
    if task_doc.sequence is None:
        task_doc.sequence = arrival_index
    siblings = frappe.get_all(
        "Warehouse Task", filters={"warehouse_order": wo_name, "docstatus": ["<", 2], "status": ["in", NON_TERMINAL_STATUSES]},
        fields=["name", "task_type", "sequence"], order_by="sequence asc",
    )
    blocker = next((s for s in siblings if (s.sequence or 0) < task_doc.sequence), None)
    if blocker:
        task_doc.status = "On Hold"
        task_doc.blocking_reason = _("Waiting on {0} ({1}) in this Warehouse Order").format(blocker.name, blocker.task_type)

def _sequence_gate_blocks(task_row):
    # Same check _gate_on_sequence applies at creation time: is there still an earlier,
    # unconfirmed sibling in this task's own Warehouse Order? Used before releasing a
    # predecessor-gated task so it doesn't jump its own Warehouse Order's queue.
    if not task_row.get("warehouse_order"): return False
    siblings = frappe.get_all("Warehouse Task", filters={
        "warehouse_order": task_row["warehouse_order"], "docstatus": ["<", 2],
        "status": ["in", NON_TERMINAL_STATUSES], "name": ["!=", task_row["name"]],
    }, fields=["sequence"])
    seq = task_row.get("sequence") or 0
    return any((s.sequence or 0) < seq for s in siblings)

def release_next_in_sequence(wo_name):
    if not wo_name: return []
    on_hold = frappe.get_all("Warehouse Task", filters={"warehouse_order": wo_name, "status": "On Hold", "docstatus": ["<", 2]},
        fields=["name", "sequence"], order_by="sequence asc")
    if not on_hold: return []
    still_blocking = frappe.get_all("Warehouse Task",
        filters={"warehouse_order": wo_name, "docstatus": ["<", 2], "status": ["in", ("Open", "Available", "Assigned", "In Process", "Partially Confirmed", "Exception")]},
        fields=["sequence"])
    blocking_sequences = [s.sequence or 0 for s in still_blocking]
    min_on_hold = min(s.sequence or 0 for s in on_hold)
    if any(seq < min_on_hold for seq in blocking_sequences): return []
    resource = frappe.db.get_value("Warehouse Order", wo_name, "assigned_resource")
    released = [s.name for s in on_hold if (s.sequence or 0) == min_on_hold]
    new_status = "Assigned" if resource else "Open"
    for name in released:
        frappe.db.set_value("Warehouse Task", name, {"status": new_status, "blocking_reason": None}, update_modified=True)
    return released

def sync_warehouse_order(wo_name):
    if not wo_name: return
    tasks = frappe.get_all("Warehouse Task", filters={"warehouse_order": wo_name}, fields=["status"])
    if not tasks: return
    confirmed = sum(1 for t in tasks if t.status in ("Confirmed", "Cancelled"))
    in_process = any(t.status in ("In Process", "Partially Confirmed", "Confirmed") for t in tasks)
    wo = frappe.get_doc("Warehouse Order", wo_name)
    updates = {"confirmed_count": confirmed}
    if confirmed >= len(tasks):
        updates["status"] = "Completed"
        updates["completed_at"] = now_datetime()
    elif in_process and wo.status in ("Open", "Assigned"):
        updates["status"] = "In Process"
        updates["started_at"] = updates.get("started_at") or now_datetime()
    wo.db_set(updates, update_modified=True)

def join_queue(queue_name, user=None):
    require_role(*RESOURCE_ROLES)
    user = user or frappe.session.user
    resource = frappe.db.get_value("WMS Resource", {"user": user, "active": 1}, "name")
    if not resource: frappe.throw(_("No active WMS Resource is linked to your user"))
    queue = frappe.get_doc("Warehouse Queue", queue_name)
    resource_doc = frappe.get_doc("WMS Resource", resource)
    if queue.warehouse != resource_doc.warehouse: frappe.throw(_("That queue belongs to a different warehouse"))
    resource_doc.db_set("current_queue", queue_name, update_modified=True)
    return {"resource": resource, "queue": queue_name}

def leave_queue(user=None):
    require_role(*RESOURCE_ROLES)
    user = user or frappe.session.user
    resource = frappe.db.get_value("WMS Resource", {"user": user, "active": 1}, "name")
    if resource: frappe.db.set_value("WMS Resource", resource, "current_queue", None)
    return {"resource": resource, "queue": None}

def list_queues(warehouse=None, activity=None):
    require_role(*RESOURCE_ROLES)
    filters = {"active": 1}
    if warehouse: filters["warehouse"] = warehouse
    if activity: filters["activity"] = activity
    return frappe.get_all("Warehouse Queue", filters=filters, fields=["name", "queue_code", "queue_name", "activity", "warehouse"])

def _my_resource(user=None):
    user = user or frappe.session.user
    return frappe.db.get_value("WMS Resource", {"user": user, "active": 1}, ["name", "warehouse", "current_queue"], as_dict=True)

WO_LIST_FIELDS = ["name", "activity", "queue", "priority", "status", "task_count", "confirmed_count", "wave", "creation"]

def list_my_warehouse_orders(user=None):
    require_role(*RESOURCE_ROLES)
    resource = _my_resource(user)
    if not resource: return {"resource": None, "warehouse_orders": []}
    mine = frappe.get_all("Warehouse Order",
        filters={"status": ["in", OPEN_WO_STATUSES], "assigned_resource": resource.name},
        fields=WO_LIST_FIELDS, order_by="creation asc", limit=50)
    wos = list(mine)
    if resource.current_queue:
        unassigned = frappe.get_all("Warehouse Order",
            filters={"status": "Open", "warehouse": resource.warehouse, "queue": resource.current_queue, "assigned_resource": ["in", ["", None]]},
            fields=WO_LIST_FIELDS, order_by="creation asc", limit=50)
        seen = {w.name for w in wos}
        wos += [w for w in unassigned if w.name not in seen]
    return {"resource": resource, "warehouse_orders": _by_priority_then_age(wos)}

def pull_next_warehouse_order(user=None):
    require_role(*RESOURCE_ROLES)
    resource = _my_resource(user)
    if not resource: frappe.throw(_("No active WMS Resource is linked to your user"))
    if not resource.current_queue: frappe.throw(_("Join a queue before pulling work"))
    candidates = frappe.get_all("Warehouse Order",
        filters={"status": "Open", "warehouse": resource.warehouse, "queue": resource.current_queue, "assigned_resource": ["in", ["", None]]},
        fields=["name", "priority", "creation"], order_by="creation asc")
    if not candidates: return None
    wo_name = _by_priority_then_age(candidates)[0].name
    frappe.db.set_value("Warehouse Order", wo_name, "assigned_resource", resource.name)
    frappe.db.set_value("Warehouse Order", wo_name, "status", "Assigned")
    frappe.db.set_value("Warehouse Task", {"warehouse_order": wo_name}, "assigned_resource", resource.name)
    return wo_name

def warehouse_order_detail(wo_name):
    require_role(*RESOURCE_ROLES)
    wo = frappe.get_doc("Warehouse Order", wo_name)
    tasks = frappe.get_all("Warehouse Task", filters={"warehouse_order": wo_name},
        fields=["name", "task_type", "warehouse", "product", "planned_quantity", "confirmed_quantity",
            "stock_uom", "source_bin", "destination_bin", "source_hu", "destination_hu",
            "priority", "status", "sequence", "creation"],
        order_by="sequence asc, creation asc")
    doc = wo.as_dict()
    doc["tasks"] = tasks
    return doc
