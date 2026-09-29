import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.utils import require_role

RESOURCE_ROLES = ("WMS Operator", "WMS Receiver", "WMS Picker", "WMS Packer", "WMS Loader", "WMS Supervisor")
OPEN_WO_STATUSES = ("Open", "Assigned", "In Process")
PRIORITY_RANK = {"Urgent": 0, "High": 1, "Normal": 2, "Low": 3}

def _by_priority_then_age(rows):
    return sorted(rows, key=lambda r: (PRIORITY_RANK.get(r.priority, 2), r.creation))

def determine_queue(warehouse, activity, storage_type=None, activity_area=None):
    # Narrowest match wins, same idiom as every other rule table in this app: a queue scoped
    # to this exact Activity Area beats one scoped only to the broader Storage Type, which
    # beats a blank-everything fallback queue.
    filters = {"warehouse": warehouse, "activity": activity, "active": 1}
    if activity_area:
        queue = frappe.db.get_value("Warehouse Queue", {**filters, "activity_area": activity_area}, "name")
        if queue: return queue
    if storage_type:
        queue = frappe.db.get_value("Warehouse Queue", {**filters, "activity_area": ["in", ["", None]], "storage_type": storage_type}, "name")
        if queue: return queue
    return frappe.db.get_value("Warehouse Queue", {**filters, "activity_area": ["in", ["", None]], "storage_type": ["in", ["", None]]}, "name")

def _matching_wo_creation_rule(warehouse, activity, item_group=None, stock_type=None):
    for rule in frappe.get_all("WO Creation Rule", filters={"warehouse": warehouse, "activity": activity, "active": 1},
            fields=["name", "item_group", "stock_type", "maximum_tasks", "maximum_weight", "maximum_volume",
                "standard_minutes_per_task", "maximum_minutes", "pick_hu_type"], order_by="priority asc"):
        if rule.item_group and rule.item_group != item_group: continue
        if rule.stock_type and rule.stock_type != stock_type: continue
        return rule
    return None

def _task_weight_and_volume(task_doc):
    # Per SAP EWM's own weight/volume-capped Warehouse Order Creation Rules - WMS Product
    # already carries gross_weight_per_unit/volume_per_unit (used for HU measurement rollups
    # in services/handling_unit.py); this is simply that same per-unit data times a task's own
    # planned_quantity, so a rule's Maximum Weight/Volume has something real to compare against.
    if not task_doc.product: return 0, 0
    per_unit = frappe.db.get_value("WMS Product", {"item": task_doc.product}, ["gross_weight_per_unit", "volume_per_unit"], as_dict=True)
    if not per_unit: return 0, 0
    qty = flt(task_doc.planned_quantity)
    return flt(per_unit.gross_weight_per_unit) * qty, flt(per_unit.volume_per_unit) * qty

def _batch_key_with_room(queue, batch_key, rule, weight_increment=0, volume_increment=0, minutes_increment=0):
    # A WO Creation Rule caps how many tasks (and now, optionally, how much weight/volume/
    # estimated time) one Warehouse Order can hold. batch_key alone normally guarantees reuse of
    # the same WO; once a rule caps any of these, later tasks spill into a fresh WO under a
    # derived batch_key instead of piling onto a full one. A brand-new candidate batch_key (no
    # existing WO yet) always has room - a single task heavier than the limit itself must still
    # go somewhere, so it gets its own WO rather than looping forever looking for space.
    suffix = 0
    while True:
        candidate = batch_key if suffix == 0 else f"{batch_key}#{suffix}"
        existing = frappe.db.get_value("Warehouse Order", {"queue": queue, "batch_key": candidate, "status": ["in", OPEN_WO_STATUSES]},
            ["task_count", "total_weight", "total_volume", "estimated_minutes"], as_dict=True)
        if not existing:
            return candidate
        over_tasks = rule.maximum_tasks and (existing.task_count or 0) >= rule.maximum_tasks
        over_weight = rule.maximum_weight and (flt(existing.total_weight) + weight_increment) > rule.maximum_weight
        over_volume = rule.maximum_volume and (flt(existing.total_volume) + volume_increment) > rule.maximum_volume
        over_minutes = rule.maximum_minutes and (flt(existing.estimated_minutes) + minutes_increment) > rule.maximum_minutes
        if not (over_tasks or over_weight or over_volume or over_minutes):
            return candidate
        suffix += 1

def _increment_wo_totals(wo_name, weight_increment, volume_increment, minutes_increment):
    if not (weight_increment or volume_increment or minutes_increment): return
    current = frappe.db.get_value("Warehouse Order", wo_name, ["total_weight", "total_volume", "estimated_minutes"], as_dict=True)
    frappe.db.set_value("Warehouse Order", wo_name, {
        "total_weight": flt(current.total_weight) + weight_increment,
        "total_volume": flt(current.total_volume) + volume_increment,
        "estimated_minutes": flt(current.estimated_minutes) + minutes_increment,
    })

def get_or_create_warehouse_order(warehouse, activity, queue, batch_key, priority="Normal", wave=None, reference_doctype=None, reference_name=None,
        item_group=None, stock_type=None, rule=None, weight_increment=0, volume_increment=0, minutes_increment=0, destination_bin=None):
    # Never auto-assigns a resource at creation - a Warehouse Order sits Open, scoped only to
    # its queue, until a resource explicitly claims it (pulling the next one, or confirming a
    # task on it manually). Resources only execute things; assignment is never the default.
    if rule is None:
        rule = _matching_wo_creation_rule(warehouse, activity, item_group, stock_type)
    if rule and (rule.maximum_tasks or rule.maximum_weight or rule.maximum_volume or rule.maximum_minutes):
        batch_key = _batch_key_with_room(queue, batch_key, rule, weight_increment, volume_increment, minutes_increment)
    existing = frappe.db.get_value("Warehouse Order", {"queue": queue, "batch_key": batch_key, "status": ["in", OPEN_WO_STATUSES]}, "name")
    if existing: return existing
    wo = frappe.get_doc({
        "doctype": "Warehouse Order", "warehouse": warehouse, "activity": activity, "queue": queue,
        "batch_key": batch_key, "wave": wave, "priority": priority,
        "assigned_resource": None, "status": "Open",
        "reference_doctype": reference_doctype, "reference_name": reference_name,
    })
    wo.insert(ignore_permissions=True)
    if activity == "Pick" and rule and rule.pick_hu_type and destination_bin:
        # SAP EWM's packing profile: every task this Warehouse Order ever bundles shares one
        # destination HU, created once right here rather than left to whatever the first picker
        # happens to scan. hu_number is a throwaway placeholder for an External-numbering HU
        # Type - the controller's before_insert always replaces it for an Internal one.
        pick_hu = frappe.get_doc({
            "doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10),
            "hu_type": rule.pick_hu_type, "current_bin": destination_bin, "warehouse": warehouse,
        })
        pick_hu.flags.wms_service_update = True
        pick_hu.insert(ignore_permissions=True)
        frappe.db.set_value("Warehouse Order", wo.name, "pick_handling_unit", pick_hu.name)
    return wo.name

# Which bin actually drives queue routing depends on where the physical work happens for that
# task type, not a single order that fits every activity: Internal Move's own test suite already
# documents (and relies on) source_bin winning - the source zone is the meaningful one when a
# picker just needs to know where to go get something. Putaway is the opposite: source_bin is
# always some generic receiving dock, never the zone a putaway worker actually cares about, which
# is wherever the item is going TO. Reproduced live in production: every Putaway task landed with
# no warehouse_order/queue at all, because source_bin (the dock, always non-blank, always has a
# storage_type) resolved first and destination_bin (the real storage zone DC1-PUTAWAY-Q was
# actually scoped to) never got a look in.
DESTINATION_DRIVEN_TASK_TYPES = {"Putaway"}

def attach_task(task_doc, batch_key, reference_doctype=None, reference_name=None):
    # Called on an unsaved Warehouse Task before insert; sets warehouse_order/queue/assigned_resource
    # in place. No-op (task stays unqueued, back-compat) if no queue is configured for this activity.
    storage_type, activity_area = None, None
    bin_fields = ("destination_bin", "source_bin") if task_doc.task_type in DESTINATION_DRIVEN_TASK_TYPES else ("source_bin", "destination_bin")
    for bin_field in bin_fields:
        bin_name = task_doc.get(bin_field)
        if bin_name:
            storage_type, activity_area = frappe.db.get_value("Storage Bin", bin_name, ["storage_type", "activity_area"])
            if storage_type: break
    queue = determine_queue(task_doc.warehouse, task_doc.task_type, storage_type, activity_area)
    if not queue: return
    item_group = frappe.db.get_value("Item", task_doc.product, "item_group") if task_doc.product else None
    stock_type = task_doc.get("stock_type_from")
    rule = _matching_wo_creation_rule(task_doc.warehouse, task_doc.task_type, item_group, stock_type)
    weight_increment, volume_increment = _task_weight_and_volume(task_doc)
    minutes_increment = flt(rule.standard_minutes_per_task) if rule and rule.standard_minutes_per_task else 0
    wo_name = get_or_create_warehouse_order(
        task_doc.warehouse, task_doc.task_type, queue, batch_key,
        priority=task_doc.priority or "Normal", wave=task_doc.get("wave"),
        reference_doctype=reference_doctype, reference_name=reference_name,
        item_group=item_group, stock_type=stock_type, rule=rule,
        weight_increment=weight_increment, volume_increment=volume_increment, minutes_increment=minutes_increment,
        destination_bin=task_doc.get("destination_bin"),
    )
    wo = frappe.get_cached_doc("Warehouse Order", wo_name)
    task_doc.warehouse_order = wo_name
    task_doc.queue = queue
    task_doc.assigned_resource = wo.assigned_resource
    if wo.assigned_resource: task_doc.status = "Assigned"
    task_count = frappe.db.get_value("Warehouse Order", wo_name, "task_count") or 0
    frappe.db.set_value("Warehouse Order", wo_name, "task_count", task_count + 1)
    _increment_wo_totals(wo_name, weight_increment, volume_increment, minutes_increment)
    if task_doc.task_type == "Pick" and not task_doc.destination_hu:
        # A freshly read value, not the cached wo above - pick_handling_unit may have just been
        # set by get_or_create_warehouse_order in this same call, on this same WO.
        pick_hu = frappe.db.get_value("Warehouse Order", wo_name, "pick_handling_unit")
        if pick_hu: task_doc.destination_hu = pick_hu
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
    tasks = frappe.get_all("Warehouse Task", filters={"warehouse_order": wo_name}, fields=["status", "sequence", "blocking_reason"])
    if not tasks: return
    wo = frappe.get_doc("Warehouse Order", wo_name)
    if wo.status == "On Hold": return  # a deliberate Supervisor pause always wins over the automatic recompute
    confirmed = sum(1 for t in tasks if t.status in ("Confirmed", "Cancelled"))
    in_process = any(t.status in ("In Process", "Partially Confirmed", "Confirmed") for t in tasks)
    updates = {"confirmed_count": confirmed}
    if confirmed >= len(tasks):
        updates["status"] = "Completed"
        updates["blocking_reason"] = None
        updates["completed_at"] = now_datetime()
        # A WO whose only sync call already finds every task Confirmed (e.g. a single-task
        # WO, or several tasks confirmed together) never passes through the elif below, so
        # started_at would otherwise be left permanently null - record it as equal to
        # completed_at rather than never set at all.
        if not wo.started_at: updates["started_at"] = updates["completed_at"]
    else:
        non_terminal = sorted((t for t in tasks if t.status in NON_TERMINAL_STATUSES), key=lambda t: t.sequence or 0)
        lead = non_terminal[0] if non_terminal else None
        if lead and lead.status == "Exception":
            updates["status"] = "Blocked"
            updates["blocking_reason"] = lead.blocking_reason or _("The current task hit an exception")
        elif in_process:
            updates["status"] = "In Process"
            updates["blocking_reason"] = None
            if wo.status in ("Open", "Assigned"): updates["started_at"] = now_datetime()
        elif wo.status == "Blocked":
            # The lead task's exception resolved (or a new, non-exception task became the
            # lead) - fall back to whatever status the WO would otherwise be in.
            updates["status"] = "In Process" if wo.started_at else "Open"
            updates["blocking_reason"] = None
    wo.db_set(updates, update_modified=True)

def block_warehouse_order(wo_name, reason=None):
    # A deliberate Supervisor pause - distinct from the automatic "Blocked" the recompute above
    # sets on an Exception, and never overwritten by it (sync_warehouse_order returns early on
    # a WO that's already On Hold).
    require_role("WMS Supervisor")
    frappe.db.set_value("Warehouse Order", wo_name, {"status": "On Hold", "blocking_reason": reason or _("On hold")})
    return {"warehouse_order": wo_name, "status": "On Hold"}

def resume_warehouse_order(wo_name):
    require_role("WMS Supervisor")
    frappe.db.set_value("Warehouse Order", wo_name, {"status": "Open", "blocking_reason": None})
    sync_warehouse_order(wo_name)
    return {"warehouse_order": wo_name, "status": frappe.db.get_value("Warehouse Order", wo_name, "status")}

def join_queue(queue_name, user=None):
    require_role(*RESOURCE_ROLES)
    user = user or frappe.session.user
    resource = frappe.db.get_value("WMS Resource", {"user": user, "active": 1}, "name")
    if not resource: frappe.throw(_("No active WMS Resource is linked to your user"))
    queue = frappe.get_doc("Warehouse Queue", queue_name)
    resource_doc = frappe.get_doc("WMS Resource", resource)
    if queue.warehouse != resource_doc.warehouse: frappe.throw(_("That queue belongs to a different warehouse"))
    # Skipped when either side has no group set, so an ungrouped resource/queue (not yet
    # configured, or deliberately warehouse-wide) keeps working exactly as before.
    if resource_doc.resource_group and queue.resource_group and resource_doc.resource_group != queue.resource_group:
        frappe.throw(_("That queue belongs to a different Resource Group than yours"))
    resource_doc.db_set("current_queue", queue_name, update_modified=True)
    return {"resource": resource, "queue": queue_name}

def leave_queue(user=None):
    require_role(*RESOURCE_ROLES)
    user = user or frappe.session.user
    resource = frappe.db.get_value("WMS Resource", {"user": user, "active": 1}, "name")
    if resource: frappe.db.set_value("WMS Resource", resource, "current_queue", None)
    return {"resource": resource, "queue": None}

def list_queues(warehouse=None, activity=None, user=None):
    require_role(*RESOURCE_ROLES)
    filters = {"active": 1}
    if warehouse: filters["warehouse"] = warehouse
    if activity: filters["activity"] = activity
    # Scoped to the calling resource's own Resource Group when it has one - an ungrouped
    # resource (or one with no matching group queues) still sees every active queue in the
    # warehouse, same as before, so nothing breaks for a site that hasn't configured groups.
    resource_group = frappe.db.get_value("WMS Resource", {"user": user or frappe.session.user, "active": 1}, "resource_group")
    if resource_group and frappe.db.exists("Warehouse Queue", {**filters, "resource_group": resource_group}):
        filters["resource_group"] = resource_group
    return frappe.get_all("Warehouse Queue", filters=filters, fields=["name", "queue_code", "queue_name", "activity", "warehouse"])

def _my_resource(user=None):
    user = user or frappe.session.user
    return frappe.db.get_value("WMS Resource", {"user": user, "active": 1}, ["name", "warehouse", "current_queue", "resource_group"], as_dict=True)

def _eligible_queues(resource):
    # current_queue is an optional focus on one specific queue; without it, every active queue
    # in the resource's own Resource Group is fair game (see _resource_for_queue's identical
    # fallback). An ungrouped resource with no current_queue is eligible for nothing - same as
    # today's "join a queue first" requirement, just no longer the *only* path to eligibility.
    if resource.current_queue: return [resource.current_queue]
    if resource.resource_group:
        return frappe.get_all("Warehouse Queue", filters={"resource_group": resource.resource_group, "warehouse": resource.warehouse, "active": 1}, pluck="name")
    return []

WO_LIST_FIELDS = ["name", "activity", "queue", "priority", "status", "task_count", "confirmed_count", "wave", "creation"]

def list_my_warehouse_orders(user=None):
    require_role(*RESOURCE_ROLES)
    resource = _my_resource(user)
    if not resource: return {"resource": None, "warehouse_orders": []}
    mine = frappe.get_all("Warehouse Order",
        filters={"status": ["in", OPEN_WO_STATUSES], "assigned_resource": resource.name},
        fields=WO_LIST_FIELDS, order_by="creation asc", limit=50)
    wos = list(mine)
    queues = _eligible_queues(resource)
    if queues:
        unassigned = frappe.get_all("Warehouse Order",
            filters={"status": "Open", "warehouse": resource.warehouse, "queue": ["in", queues], "assigned_resource": ["in", ["", None]]},
            fields=WO_LIST_FIELDS, order_by="creation asc", limit=50)
        seen = {w.name for w in wos}
        wos += [w for w in unassigned if w.name not in seen]
    return {"resource": resource, "warehouse_orders": _by_priority_then_age(wos)}

def pull_next_warehouse_order(user=None):
    require_role(*RESOURCE_ROLES)
    resource = _my_resource(user)
    if not resource: frappe.throw(_("No active WMS Resource is linked to your user"))
    queues = _eligible_queues(resource)
    if not queues: frappe.throw(_("Join a queue, or ask a supervisor to add your Resource Group to one, before pulling work"))
    candidates = frappe.get_all("Warehouse Order",
        filters={"status": "Open", "warehouse": resource.warehouse, "queue": ["in", queues], "assigned_resource": ["in", ["", None]]},
        fields=["name", "priority", "creation"], order_by="creation asc")
    for candidate in _by_priority_then_age(candidates):
        # Atomic compare-and-set: two resources pulling at the same instant must never both walk
        # away believing they own the same Warehouse Order - the read above is just a candidate
        # list, not a reservation. A 0-row UPDATE means someone else claimed this one between
        # that read and this write; fall through to the next candidate instead of trusting it.
        #
        # Under real concurrent load this conditional UPDATE can also raise a genuine
        # QueryDeadlockError (MySQL/MariaDB error 1020/1213) instead of cleanly returning 0 rows -
        # reproduced live with two resources pulling at the same instant. Worse, when MySQL picks
        # this transaction as the deadlock *victim* it can discard every savepoint in it as part
        # of that resolution - a savepoint-scoped catch around just this UPDATE isn't safe here,
        # because the *next* statement (release/rollback to that now-gone savepoint) then throws
        # its own unrelated "SAVEPOINT ... does not exist" (error 1305), reproduced live right
        # after fixing the first error this same way. Whatever the exact failure, by the time any
        # exception reaches here the connection's transaction state is no longer trustworthy
        # enough to keep trying more candidates in it - roll the whole thing back (nothing else
        # was written earlier in this call) and tell the caller no work is available right now,
        # the same outcome as an ordinary lost race, instead of a raw 500.
        try:
            frappe.db.sql(
                "update `tabWarehouse Order` set assigned_resource=%s, status='Assigned', modified=%s, modified_by=%s "
                "where name=%s and status='Open' and (assigned_resource is null or assigned_resource='')",
                (resource.name, now_datetime(), frappe.session.user, candidate.name),
            )
            claimed = bool(frappe.db.sql("select row_count()")[0][0])
        except Exception:
            frappe.db.rollback()
            return None
        if claimed:
            frappe.db.set_value("Warehouse Task", {"warehouse_order": candidate.name}, "assigned_resource", resource.name)
            return candidate.name
    return None

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
