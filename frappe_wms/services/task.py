import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import transfer_stock, release_allocation, dim_values, OWNER_KEYS
from frappe_wms.services.determination import determine_destination_bin, determine_process_type
from frappe_wms.services.bin_rules import hu_requirement, incoming_load, validate_destination_bin
from frappe_wms.services.warehouse_order import attach_task, sync_warehouse_order, release_next_in_sequence, _sequence_gate_blocks, _eligible_queues, RESOURCE_ROLES
from frappe_wms.services.storage_process import advance_to_next_step
from frappe_wms.services.printing import create_print_spool
from frappe_wms.utils import require_role

TASK_TYPE_BY_REQUEST = {
    "Unload": "Unload", "Putaway": "Putaway", "Pick": "Pick", "Replenish": "Putaway",
    "Internal Move": "Internal Move", "Stage": "Stage", "Load": "Load",
    "Unload Vehicle": "Unload", "Posting Change": "Posting Change", "Inventory Count": "Inventory Count",
    "Cross Dock": "Cross Dock",
}

def _split_by_full_pallet(remaining, full_qty):
    # SAP EWM-style task splitting: a request for more than one full pallet's worth becomes one
    # task per full pallet (plus a remainder task), each free to land in its own bin, rather than
    # one task instructing a resource to move an unrealistically large quantity in one go.
    if not full_qty or remaining <= full_qty: return [remaining]
    chunks, left = [], remaining
    while left > 0:
        chunks.append(min(left, full_qty))
        left -= full_qty
    return chunks

def plan_requests(request_names, batch_key=None):
    """Creates the tasks of each request. One that cannot be planned now (no free destination
    bin - a full storage type, a missing rule) stays an open request instead of undoing the
    goods receipt / kit / delivery change that raised it: the Monitor lists it under Warehouse
    Requests Without Tasks and plans it again with "Create tasks" (SAP: the warehouse request
    stays open with its error log). -> (task names, unplanned request names)"""
    tasks, unplanned = [], []
    for name in request_names:
        frappe.db.savepoint("wms_plan_request")
        try:
            created = create_tasks_for_request(name, batch_key=batch_key) if batch_key else create_tasks_for_request(name)
        except frappe.ValidationError as e:
            frappe.db.rollback(save_point="wms_plan_request")
            frappe.clear_messages()
            frappe.get_doc("Warehouse Request", name).add_comment("Comment", _("Tasks could not be created yet: {0}").format(e))
            unplanned.append(name)
            continue
        tasks += created if isinstance(created, list) else [created] if created else []
    return tasks, unplanned

def create_tasks_for_request(request_name, batch_key=None):
    request = frappe.get_doc("Warehouse Request", request_name, for_update=True)
    if request.status not in {"Draft", "Open", "Partially Tasked"}: frappe.throw(_("Warehouse Request is not open for tasking"))
    remaining = flt(request.requested_quantity) - flt(request.created_quantity)
    if remaining <= 0: frappe.throw(_("Warehouse Request is already fully tasked"))
    task_type = TASK_TYPE_BY_REQUEST.get(request.request_type)
    if not task_type: frappe.throw(_("No warehouse task type mapped for request type {0}").format(request.request_type))
    process_type = frappe.get_cached_doc("Warehouse Process Type", request.process_type) if request.process_type else None
    movement_type = process_type.movement_type if process_type else None
    if not movement_type: frappe.throw(_("Warehouse Process Type must define a movement type before tasking"))

    full_qty = None
    # Splitting by full-pallet quantity only makes sense when the system still has to pick a
    # destination bin (and, implicitly, a destination HU) per chunk - a request whose
    # destination_bin/destination_hu is already fixed (the normal Replenish/direct-move case: a
    # specific pick-face bin, or an existing HU to add into) can only ever become one task,
    # however large, since there is nowhere else for a second chunk to go.
    if process_type and process_type.destination_required and not request.destination_bin and not request.destination_hu:
        from frappe_wms.services.handling_unit import full_hu_quantity  # local: handling_unit imports task.my_resource
        full_qty = full_hu_quantity(request.product)
    chunks = _split_by_full_pallet(remaining, full_qty)
    batch_key = batch_key or frappe.generate_hash(length=10)
    reserved_hu_counts = {}
    created = []
    source_bin = request.source_bin or process_type.default_source_bin
    priority = request.priority if request.priority and request.priority != "Normal" else (process_type.default_priority or "Normal")
    for chunk_qty in chunks:
        destination_bin = request.destination_bin or process_type.default_destination_bin
        if not destination_bin and process_type and process_type.destination_required:
            hu_type = frappe.db.get_value("Handling Unit", request.source_hu, "hu_type") if request.source_hu else None
            source_storage_type = frappe.db.get_value("Storage Bin", source_bin, "storage_type") if source_bin else None
            incoming_weight, incoming_volume = incoming_load(request.product, chunk_qty)
            destination_bin = determine_destination_bin({"warehouse": request.warehouse, "activity": process_type.activity, "item": request.product,
                "stock_type": request.stock_type, "hu_type": hu_type, "source_storage_type": source_storage_type,
                "incoming_weight": incoming_weight, "incoming_volume": incoming_volume, "incoming_quantity": chunk_qty, "destination_hu": request.destination_hu or request.source_hu,
                "forced_storage_type": process_type.default_destination_storage_type, "reserved_hu_counts": reserved_hu_counts,
                "receipt_origin": request.get("receipt_origin"), "production_supply_area": request.get("production_supply_area")})
            reserved_hu_counts[destination_bin] = reserved_hu_counts.get(destination_bin, 0) + 1
        idempotency_key = f"WT:{request.name}" if len(chunks) == 1 else f"WT:{request.name}:{len(created) + 1}"
        task = frappe.get_doc({"doctype": "Warehouse Task", "warehouse_request": request.name, "task_type": task_type, "warehouse": request.warehouse, "product": request.product, "planned_quantity": chunk_qty, "stock_uom": request.stock_uom, "source_bin": source_bin, "destination_bin": destination_bin, "source_hu": request.source_hu, "destination_hu": request.destination_hu, "batch_no": request.batch_no, "serial_no": request.serial_no, "stock_type_from": request.stock_type, "stock_type_to": request.stock_type, **dim_values(request), "movement_type": movement_type, "priority": priority, "status": "Open", "idempotency_key": idempotency_key})
        from frappe_wms.services.layout_control import reroute
        reroute(task)  # layout-oriented storage control: via an intermediate bin when a rule applies
        attach_task(task, batch_key, reference_doctype="Warehouse Request", reference_name=request.name, default_queue=process_type.default_queue)
        task.insert(ignore_permissions=True)
        created.append(task.name)
    frappe.db.set_value("Warehouse Request", request.name, {"created_quantity": flt(request.created_quantity) + remaining, "status": "Fully Tasked"})
    return created[0] if len(created) == 1 else created

# confirm_task's destination_hu resolution defaults an unspecified destination to "the same HU
# it came from" - correct for a task confirmed later at the RF, where a resource is physically
# carrying an HU to a new bin and simply hasn't scanned a different one. create_and_confirm_move
# has no such later confirm step - its caller's destination_hu (or lack of one) is the whole and
# final word - so when it means "no HU, make it loose" it must say so unambiguously, not rely on
# a bare None that confirm_task's fallback chain would otherwise silently reinterpret as "keep
# the source HU" (reproduced: unpacking part of an HU's stock into a different bin left the
# moved quantity still tagged to the source HU, now claiming to be in a bin that HU never
# entered).
_UNPACK = "\x00unpack\x00"

def create_and_confirm_move(*, warehouse, product, quantity, stock_uom, stock_type, source_bin=None, source_hu=None, destination_bin, destination_hu=None, batch_no=None, serial_no=None, device=None, scanned_source=None, scanned_destination=None):
    # The ad-hoc "move this HU/bin's stock to that bin now" action an RF operator does
    # directly from the floor (SAP EWM's immediate/direct TO creation), as opposed to a
    # planned task generated by putaway/picking/replenishment. Creates and confirms the
    # Warehouse Task as one action since there is no separate planning step to wait on.
    require_role("WMS Operator", "WMS Supervisor")
    if source_hu and source_bin and not destination_hu:
        # Moving everything an HU holds moves the HU itself; stock only lands loose when part of it moves (repack/unpack).
        held = frappe.db.sql("select product, sum(quantity) from `tabWMS Stock Balance` where handling_unit=%s and storage_bin=%s and quantity>0 group by product", (source_hu, source_bin))
        if len(held) == 1 and held[0][0] == product and flt(quantity) >= flt(held[0][1]) - 0.000001: destination_hu = source_hu
    hu_type = frappe.db.get_value("Handling Unit", source_hu, "hu_type") if source_hu else None
    incoming_weight, incoming_volume = incoming_load(product, quantity)
    validate_destination_bin(destination_bin, item=product, incoming_quantity=quantity, stock_type=stock_type, hu_type=hu_type,
        batch_no=batch_no, destination_hu=destination_hu or source_hu, incoming_weight=incoming_weight, incoming_volume=incoming_volume)
    process_type_name = determine_process_type(warehouse, "Internal Move", item=product, stock_type=stock_type, default="INTERNAL_MOVE")
    process_type = frappe.get_cached_doc("Warehouse Process Type", process_type_name)
    task = frappe.get_doc({
        "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": warehouse, "product": product,
        "planned_quantity": quantity, "stock_uom": stock_uom, "batch_no": batch_no, "serial_no": serial_no,
        "source_bin": source_bin, "destination_bin": destination_bin, "source_hu": source_hu,
        # a destination HU barcode nobody registered yet is created by confirm_task below, so the task (a Link) can't hold it yet
        "destination_hu": destination_hu if destination_hu and frappe.db.exists("Handling Unit", destination_hu) else None,
        "stock_type_from": stock_type, "stock_type_to": stock_type, "movement_type": process_type.movement_type,
        "priority": "Normal", "status": "Open",
    })
    task.insert(ignore_permissions=True)
    # scanned_source/scanned_destination here are only ever the RF app's check-digit entry (the
    # bin itself is already this call's own source_bin/destination_bin, not something separate to
    # re-match) - confirm_task's check-digit branch below is what actually verifies them.
    return confirm_task(task.name, scanned_source=scanned_source, scanned_destination=scanned_destination,
        confirmed_quantity=quantity, device=device, destination_hu=destination_hu or _UNPACK)

def create_pick_tasks(delivery_name, strategy="Single Order"):
    require_role("WMS Operator", "WMS Picker", "WMS Supervisor")
    delivery = frappe.get_doc("Outbound Delivery", delivery_name)
    if delivery.docstatus != 1: frappe.throw(_("Outbound Delivery must be submitted before picking tasks can be created"))
    if not delivery.staging_bin: frappe.throw(_("Outbound Delivery must have a staging bin before picking tasks can be created"))
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": delivery_name, "status": "Allocated"}, fields=["*"])
    if not allocations: frappe.throw(_("No open allocations to create pick tasks for"))
    created = _create_pick_tasks_from_allocations(allocations, strategy)
    delivery.db_set("status", "Picking")
    return created

def create_pick_tasks_for_wave(delivery_names, strategy="Single Order", wave=None):
    require_role("WMS Operator", "WMS Picker", "WMS Supervisor")
    for delivery_name in delivery_names:
        docstatus, staging_bin = frappe.db.get_value("Outbound Delivery", delivery_name, ["docstatus", "staging_bin"])
        if docstatus != 1: frappe.throw(_("Outbound Delivery {0} must be submitted before picking tasks can be created").format(delivery_name))
        if not staging_bin:
            frappe.throw(_("Outbound Delivery {0} must have a staging bin before picking tasks can be created").format(delivery_name))
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": ["in", delivery_names], "status": "Allocated"}, fields=["*"])
    if not allocations: frappe.throw(_("No open allocations to create pick tasks for"))
    created = _create_pick_tasks_from_allocations(allocations, strategy, wave=wave)
    for delivery_name in delivery_names:
        frappe.db.set_value("Outbound Delivery", delivery_name, "status", "Picking")
    return created

def _claim_allocations(allocations):
    # State-guarded UPDATE: a double click or a wave release racing a manual release releases each allocation once; the loser finds none left.
    claimed = []
    for allocation in allocations:
        frappe.db.sql("update `tabStock Allocation` set status='Released' where name=%s and status='Allocated'", allocation.name)
        if frappe.db.sql("select row_count()")[0][0]: claimed.append(allocation)
    if not claimed: frappe.throw(_("These allocations were already released for picking"))
    return claimed

def _create_pick_tasks_from_allocations(allocations, strategy, wave=None):
    allocations = _claim_allocations(allocations)
    deliveries = {}
    for allocation in allocations:
        if allocation.outbound_delivery not in deliveries:
            deliveries[allocation.outbound_delivery] = frappe.db.get_value(
                "Outbound Delivery", allocation.outbound_delivery, ["warehouse", "staging_bin", "priority"], as_dict=True,
            )
        delivery = deliveries[allocation.outbound_delivery]
        allocation["_warehouse"] = delivery.warehouse
        allocation["_staging_bin"] = delivery.staging_bin
        allocation["_priority"] = delivery.priority

    if strategy == "Cluster":
        groups = {}
        for allocation in allocations:
            key = (allocation._warehouse, allocation.storage_bin, allocation.handling_unit, allocation.product,
                allocation.batch_no, allocation.serial_no, allocation.stock_type, allocation._staging_bin)
            groups.setdefault(key, []).append(allocation)
        groups = list(groups.values())
    else:
        groups = [[allocation] for allocation in allocations]

    batch_key = frappe.generate_hash(length=10)
    created = []
    for group in groups:
        created.append(_create_pick_task_for_group(group, wave, batch_key))
    return created

def _pick_pack_pass_hu(outbound_delivery, warehouse, staging_bin, hu_type):
    # One shared destination HU per delivery, reused across every pick line for it (a delivery
    # with several lines/products calls this once per line, same as a Warehouse Order's own
    # pick_handling_unit in attach_task - but scoped to ONE specific delivery, not a batch of
    # tasks that might span several deliveries).
    existing = frappe.db.get_value("Outbound Delivery", outbound_delivery, "pick_pack_pass_hu", for_update=True)
    if existing: return existing
    hu = frappe.get_doc({
        "doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10),
        "hu_type": hu_type, "current_bin": staging_bin, "warehouse": warehouse,
    })
    hu.flags.wms_service_update = True
    hu.insert(ignore_permissions=True)
    frappe.db.set_value("Outbound Delivery", outbound_delivery, "pick_pack_pass_hu", hu.name)
    return hu.name

def _pick_destination(process_type, first):
    # (destination_bin, destination_hu, requires_sort_after_pick, unpack_at_destination) for a
    # Pick task, branching on the matching Warehouse Process Type's picking_strategy.
    # Single-Step (default) is exactly today's behavior, unchanged: pick straight to the
    # delivery's own staging bin.
    strategy = process_type.picking_strategy or "Single-Step"
    from frappe_wms.services.determination import wms_product_warehouse
    per_warehouse = wms_product_warehouse(first.product, first._warehouse)
    if per_warehouse and per_warehouse.two_step_picking: strategy = "Two-Step"  # the product's own flag
    if strategy == "Two-Step":
        shared_bin = frappe.db.get_value("WMS Warehouse", first._warehouse, "default_picking_staging_bin")
        if not shared_bin: frappe.throw(_("WMS Warehouse {0} has no Default Picking Staging Bin configured for Two-Step Picking").format(first._warehouse))
        # Deliberately no HU, same as a Deconsolidation line splitting stock loose into a bin -
        # unpack_at_destination=1 so confirm_task never falls back to the source HU (that source
        # HU is a storage-side pallet/bin, not something that belongs sitting in a shared
        # multi-order staging area).
        return shared_bin, None, 1, 1
    if strategy == "Pick-Pack-Pass":
        if not process_type.pick_pack_pass_hu_type: frappe.throw(_("Warehouse Process Type {0} needs a Pick-Pack-Pass HU Type configured").format(process_type.name))
        hu = _pick_pack_pass_hu(first.outbound_delivery, first._warehouse, first._staging_bin, process_type.pick_pack_pass_hu_type)
        return first._staging_bin, hu, 0, 0
    return first._staging_bin, None, 0, 0

def _create_pick_task_for_group(allocations, wave, batch_key):
    first = allocations[0]
    process_type_name = determine_process_type(first._warehouse, "Pick", item=first.product, stock_type=first.stock_type, priority_level=first._priority, default="OB_PICK")
    process_type = frappe.get_cached_doc("Warehouse Process Type", process_type_name)
    stock_uom = frappe.db.get_value("WMS Stock Balance", first.stock_balance, "stock_uom")
    total_qty = sum(flt(a.allocated_quantity) for a in allocations)
    from frappe_wms.services.travel import sort_sequence
    sequence = sort_sequence(first.storage_bin, "Pick")  # the walk path of the bin's activity area, else its own sequence
    priority_order = ("Low", "Normal", "High", "Urgent")
    priority = max((a._priority or "Normal" for a in allocations), key=priority_order.index)
    destination_bin, destination_hu, requires_sort_after_pick, unpack_at_destination = _pick_destination(process_type, first)
    task = frappe.get_doc({
        "doctype": "Warehouse Task", "stock_allocation": first.name, "task_type": "Pick",
        "warehouse": first._warehouse, "product": first.product, "planned_quantity": total_qty, "stock_uom": stock_uom,
        "batch_no": first.batch_no, "serial_no": first.serial_no, "source_bin": first.storage_bin, **dim_values(first),
        "destination_bin": destination_bin, "source_hu": first.handling_unit, "destination_hu": destination_hu,
        "requires_sort_after_pick": requires_sort_after_pick, "unpack_at_destination": unpack_at_destination,
        "stock_type_from": first.stock_type, "stock_type_to": first.stock_type,
        "movement_type": process_type.movement_type, "priority": priority or "Normal", "status": "Open",
        "sequence": sequence, "wave": wave,
        "idempotency_key": "WT:" + "-".join(a.name for a in allocations),
        "stock_allocations": [{"stock_allocation": a.name, "allocated_quantity": a.allocated_quantity} for a in allocations],
    })
    attach_task(task, batch_key, reference_doctype="Outbound Delivery", reference_name=first.outbound_delivery)
    task.insert(ignore_permissions=True)
    return task.name

OPEN_TASK_STATUSES = ("Open", "On Hold", "Available", "Assigned", "In Process", "Partially Confirmed")

# Summary fields for the RF "find task by reference" lookups (picking.find_pick_tasks,
# receipt.find_putaway_tasks, ...) and the task-list cards built from them - same shape
# regardless of task_type, since the RF confirm wizard (screens/task.js) is generic too.
TASK_SUMMARY_FIELDS = ["name", "task_type", "warehouse", "product", "planned_quantity", "confirmed_quantity",
    "stock_uom", "source_bin", "destination_bin", "source_hu", "destination_hu",
    "priority", "status", "movement_type", "sequence", "queue", "wave", "warehouse_order", "assigned_resource",
    "blocking_reason"]

def task_names_for_allocations(allocation_names):
    # Every Warehouse Task tied to a set of Stock Allocations, whether picked individually
    # (Warehouse Task.stock_allocation) or as part of a cluster pick (the Warehouse Task
    # Allocation child table) - shared by the RF picking-entry lookup, the Outbound Delivery
    # cancel cascade, and the Monitor's per-delivery execution status.
    if not allocation_names: return set()
    cluster_task_names = frappe.get_all("Warehouse Task Allocation", filters={"stock_allocation": ["in", allocation_names]}, pluck="parent")
    direct_task_names = frappe.get_all("Warehouse Task", filters={"stock_allocation": ["in", allocation_names]}, pluck="name")
    return set(cluster_task_names) | set(direct_task_names)

def my_resource(user=None):
    user = user or frappe.session.user
    # Ordered on purpose: a user bound to more than one active WMS Resource (a test-setup-only
    # scenario in practice, but not one the doctype itself prevents) previously resolved to
    # whichever row MySQL happened to return first for an unordered filter - unspecified, and
    # reproduced as flakiness across this app's own test suite once enough test classes had
    # bound resources to the same user. Most-recently-modified is the same idea as "whichever
    # device I most recently picked" (services/scanner.py's pick_device flow touches modified via
    # a plain save), so it's the sensible tiebreak when this can happen at all.
    resource = frappe.db.get_value("WMS Resource", {"user": user, "active": 1}, ["name", "warehouse", "current_queue", "resource_group", "current_work_center"], as_dict=True, order_by="modified desc")
    if resource and resource.current_work_center:
        # Resolved here rather than making the RF app do a second round trip - actions that
        # want to default to "my work center's bin" (VAS generation today) just read this.
        resource["current_work_center_bin"] = frappe.db.get_value("Work Center", resource.current_work_center, "bin")
    return resource

def list_my_tasks(user=None):
    require_role(*RESOURCE_ROLES)
    resource = my_resource(user)
    filters = {"status": ["in", OPEN_TASK_STATUSES], "docstatus": 0}
    if resource: filters["warehouse"] = resource.warehouse
    fields = ["name", "task_type", "warehouse", "product", "planned_quantity", "confirmed_quantity",
        "stock_uom", "source_bin", "destination_bin", "source_hu", "destination_hu",
        "priority", "status", "movement_type", "sequence", "queue", "wave", "warehouse_order", "assigned_resource",
        "blocking_reason"]
    order_by = "priority desc, wave asc, sequence asc, creation asc"
    if not resource:
        return {"resource": resource, "tasks": frappe.get_list("Warehouse Task", filters=filters, fields=fields, order_by=order_by, limit=200)}
    # Used to fetch one global top-200 (by priority/wave/sequence/creation across the WHOLE
    # warehouse) and filter down to "mine, unrouted, or in a queue I cover" in Python afterward -
    # fine while the site-wide backlog stayed under ~200 rows, but reproduced live once it grew
    # past that: a resource's own freshly-assigned Warehouse Order (pull_next_warehouse_order had
    # genuinely just assigned it, seconds earlier) ranked behind thousands of older, unrelated
    # tasks in that single global ordering and never made it into the top 200 at all - "my tasks"
    # silently showed none of it, no error, nothing to indicate why. Query "mine" and "unclaimed,
    # in a queue/unrouted work I'm eligible for" separately, each already bounded on its own,
    # instead of one shared window the rest of the warehouse can crowd out.
    eligible = _eligible_queues(resource)
    mine = frappe.get_list("Warehouse Task", filters={**filters, "assigned_resource": resource.name}, fields=fields, order_by=order_by, limit=100)
    or_filters = [["queue", "is", "not set"]]
    if eligible: or_filters.append(["queue", "in", eligible])
    unclaimed = frappe.get_list("Warehouse Task", filters={**filters, "assigned_resource": ["in", ("", None)]},
        or_filters=or_filters, fields=fields, order_by=order_by, limit=100)
    seen = set()
    tasks = []
    for t in mine + unclaimed:
        if t.name in seen: continue
        seen.add(t.name); tasks.append(t)
    return {"resource": resource, "tasks": tasks[:100]}

def _release_short_pick_reservation(task, shortfall):
    # Whatever this task will now never confirm (planned - revised, once it closes out below
    # what was originally planned) was reserved on a WMS Stock Balance row and on one or more
    # Stock Allocations that can never be fulfilled from here - left alone, that reservation is
    # permanently stuck (nobody else can ever allocate it, and the Stock Allocation sits at
    # "Partially Picked" forever since its own allocated_quantity can never be reached). Shrinks
    # each affected Stock Allocation's own allocated_quantity down to what was truly picked, and
    # the Outbound Delivery Item down with it, so picking can still reach "Picked" for what's
    # actually real - a short pick's own follow-up replenishment already exists separately via
    # create_order_related_replenishment.
    if shortfall <= 0: return
    rows = task.get("stock_allocations") or ([frappe._dict(stock_allocation=task.stock_allocation)] if task.stock_allocation else [])
    if not rows: return
    release_allocation({"warehouse": task.warehouse, "product": task.product, "batch_no": task.batch_no,
        "serial_no": task.serial_no, "handling_unit": task.source_hu, "storage_bin": task.source_bin,
        "stock_type": task.stock_type_from, **dim_values(task)}, shortfall)
    remaining = shortfall
    delivery_names = set()
    for row in rows:
        if remaining <= 0: break
        allocation = frappe.get_doc("Stock Allocation", row.stock_allocation, for_update=True)
        outstanding = flt(allocation.allocated_quantity) - flt(allocation.picked_quantity)
        if outstanding <= 0: continue
        take = min(remaining, outstanding)
        new_allocated = flt(allocation.allocated_quantity) - take
        frappe.db.set_value("Stock Allocation", allocation.name, {
            "allocated_quantity": new_allocated,
            "status": "Picked" if flt(allocation.picked_quantity) >= new_allocated else allocation.status,
        })
        if allocation.outbound_delivery_item:
            frappe.db.sql("update `tabOutbound Delivery Item` set requested_quantity=requested_quantity-%s, allocated_quantity=allocated_quantity-%s where name=%s",
                (take, take, allocation.outbound_delivery_item))
        if allocation.outbound_delivery: delivery_names.add(allocation.outbound_delivery)
        remaining -= take
    for delivery_name in delivery_names:
        _update_delivery_picking_status(delivery_name)
        rows = frappe.db.sql("select requested_quantity, allocated_quantity from `tabOutbound Delivery Item` where parent=%s for update", delivery_name, as_dict=True)
        if rows:
            fully_allocated = all(flt(r.allocated_quantity) >= flt(r.requested_quantity) for r in rows)
            any_allocated = any(flt(r.allocated_quantity) > 0 for r in rows)
            frappe.db.set_value("Outbound Delivery", delivery_name, "allocation_status",
                "Fully Allocated" if fully_allocated else ("Partially Allocated" if any_allocated else "Not Allocated"))

def _exception_action(code):
    return code.system_action or None  # explicit only: the older "allows bin change" flag stays what it was (informational)

def _change_bin(task, new_bin):
    """CHBIN: the task goes to another bin - its destination for putaway-type tasks (the new bin must accept the goods), its source otherwise (it must hold the stock)."""
    bin_doc = frappe.db.get_value("Storage Bin", new_bin, ["warehouse", "active"], as_dict=True)
    if not bin_doc or bin_doc.warehouse != task.warehouse or not bin_doc.active: frappe.throw(_("{0} is not an active bin of warehouse {1}").format(new_bin, task.warehouse))
    from frappe_wms.services.warehouse_order import DESTINATION_DRIVEN_TASK_TYPES
    if task.task_type in DESTINATION_DRIVEN_TASK_TYPES:
        hu_type = frappe.db.get_value("Handling Unit", task.source_hu, "hu_type") if task.source_hu else None
        validate_destination_bin(new_bin, item=task.product, stock_type=task.stock_type_to or task.stock_type_from, hu_type=hu_type, batch_no=task.batch_no, destination_hu=task.source_hu, incoming_quantity=flt(task.planned_quantity) - flt(task.confirmed_quantity))
        task.db_set("destination_bin", new_bin, update_modified=True)
    else:
        if task.get("stock_allocations") or task.stock_allocation: frappe.throw(_("A pick for a delivery cannot change its source bin: report it as a short pick and let the delivery be allocated again"))
        held = frappe.db.sql("select coalesce(sum(quantity), 0) from `tabWMS Stock Balance` where storage_bin=%s and product=%s and stock_type=%s and quantity>0", (new_bin, task.product, task.stock_type_from))[0][0]
        if flt(held) < flt(task.planned_quantity) - flt(task.confirmed_quantity) - 0.000001: frappe.throw(_("{0} does not hold enough {1}").format(new_bin, task.product))
        task.db_set({"source_bin": new_bin, "source_hu": None}, update_modified=True)

def split_task(task, quantity, new_bin=None):
    """SPLT: carve quantity off an open task into a new one (optionally to another destination bin)."""
    quantity = flt(quantity)
    open_qty = flt(task.planned_quantity) - flt(task.confirmed_quantity)
    if quantity <= 0 or quantity >= open_qty: frappe.throw(_("Split off more than 0 and less than the open {0}").format(open_qty))
    if task.get("stock_allocations") or task.stock_allocation: frappe.throw(_("A pick for a delivery cannot be split: confirm what was picked and report the rest as a short pick"))
    values = {k: task.get(k) for k in ("warehouse_request", "task_type", "warehouse", "product", "stock_uom", "batch_no", "serial_no", "source_bin", "source_hu", "destination_bin", "destination_hu",
        "final_destination_bin", "stock_type_from", "stock_type_to", "movement_type", "priority", "warehouse_order", "queue", "assigned_resource", "predecessor_task", "wave", "sequence")}
    new = frappe.get_doc({"doctype": "Warehouse Task", **values, "planned_quantity": quantity, "status": task.status if task.status in ("Open", "Assigned", "On Hold") else "Open"})
    if new_bin: new.destination_bin = new_bin
    new.insert(ignore_permissions=True)
    task.db_set("planned_quantity", open_qty - quantity + flt(task.confirmed_quantity), update_modified=True)
    if task.warehouse_order: frappe.db.sql("update `tabWarehouse Order` set task_count=task_count+1 where name=%s", task.warehouse_order)
    return new.name

def raise_exception(task_name, exception_code, remarks=None, revised_quantity=None, new_bin=None, split_quantity=None):
    require_role("WMS Operator", "WMS Supervisor")
    code = frappe.get_cached_doc("WMS Exception Code", exception_code)
    if not code.active: frappe.throw(_("Exception code {0} is not active").format(exception_code))
    if code.requires_supervisor: require_role("WMS Supervisor")
    if code.requires_comment and not (remarks or "").strip(): frappe.throw(_("This exception requires a comment"))
    task = frappe.get_doc("Warehouse Task", task_name, for_update=True)
    if task.docstatus != 0: frappe.throw(_("Task is already confirmed or cancelled"))
    action = _exception_action(code)
    if action == "Change Bin":
        if not new_bin: frappe.throw(_("Enter the bin to use instead"))
        _change_bin(task, new_bin)
        task.add_comment("Comment", _("{0}: {1} - bin changed to {2}").format(exception_code, remarks or "", new_bin))
        return {"task": task.name, "status": task.status, "bin_changed": new_bin}
    if action == "Split Task":
        created = split_task(task, split_quantity, new_bin)
        task.add_comment("Comment", _("{0}: {1} - {2} split off as {3}").format(exception_code, remarks or "", flt(split_quantity), created))
        return {"task": task.name, "status": task.status, "split_task": created}
    if action == "Skip Task":
        later = frappe.db.sql("select coalesce(max(sequence), 0) from `tabWarehouse Task` where warehouse_order=%s", task.warehouse_order)[0][0] if task.warehouse_order else task.sequence or 0
        task.db_set("sequence", flt(later) + 1, update_modified=True)
        task.add_comment("Comment", _("{0}: {1} - skipped").format(exception_code, remarks or ""))
        return {"task": task.name, "status": task.status, "skipped": True}
    result = None
    if (code.allows_quantity_change or action == "Post Difference") and revised_quantity is not None:
        # Pick denial: less stock was found at the bin than planned. Whatever was already
        # confirmed was already transferred, so closing the task out at the revised (lower)
        # planned_quantity finishes it instead of leaving it stuck waiting on stock that
        # isn't there.
        revised_quantity = flt(revised_quantity)
        already_confirmed = flt(task.confirmed_quantity)
        original_planned = flt(task.planned_quantity)
        if revised_quantity < already_confirmed: frappe.throw(_("Revised quantity cannot be less than what is already confirmed"))
        if revised_quantity > original_planned: frappe.throw(_("Revised quantity cannot exceed the planned quantity"))
        task.db_set("planned_quantity", revised_quantity, update_modified=True)
        from frappe_wms.services.production_supply import on_staging_short
        on_staging_short(task, original_planned - revised_quantity)  # a staging task that now moves less: the rest is open for staging again
        if round(already_confirmed, 6) >= round(revised_quantity, 6):
            task.db_set({"status": "Confirmed", "docstatus": 1}, update_modified=True)
            if already_confirmed > 0: advance_to_next_step(task)  # nothing moved: no successor step with quantity 0
            _update_request(task.warehouse_request)
            _release_short_pick_reservation(task, original_planned - revised_quantity)
            # Only relocate the HU if this task actually moved real, ledger-backed stock
            # (already_confirmed > 0 via an earlier confirm_task call) - closing a task that
            # denied its full quantity with nothing ever confirmed has no stock movement to
            # reflect, and relocating the HU here anyway would make its current_bin lie about
            # where its physical stock actually is (reproduced: a fully-denied pick moved the
            # source HU's bin record to the delivery's staging bin with zero units following it).
            if already_confirmed > 0: _relocate_hu_for_task(task)
            sync_warehouse_order(task.warehouse_order)
            released_tasks = release_next_in_sequence(task.warehouse_order) + _release_predecessor_gated_tasks(task.name)
            result = {"task": task.name, "status": "Confirmed", "released_tasks": released_tasks}
            shortfall = original_planned - revised_quantity
            if shortfall > 0:
                # A logged SAP EWM Difference Analyzer-style record of what never got confirmed -
                # previously this just vanished into a lowered planned_quantity with no trace.
                # Nothing to post (the shortfall never physically existed to move); a supervisor
                # acknowledges it later via services/difference.clear_short_difference.
                from frappe_wms.services.difference import record_short_difference
                result["difference"] = record_short_difference(task, shortfall, original_planned, exception_code, remarks)
    if result is None:
        task.db_set({"status": "Exception", "exception_code": exception_code, "blocking_reason": remarks}, update_modified=True)
        sync_warehouse_order(task.warehouse_order)
        result = {"task": task.name, "status": task.status}
    if code.follow_up_action == "Create Follow-up Task" and task.task_type == "Pick":
        from frappe_wms.services.replenishment import create_order_related_replenishment
        create_order_related_replenishment(task)
    return result

def _claim_warehouse_order_if_unassigned(task, user=None):
    # A Warehouse Order never auto-assigns at creation (see get_or_create_warehouse_order) -
    # it's claimed the moment someone actually starts confirming work on it, whether that task
    # was found via Auto-pull (which already claims it) or Manual search (which doesn't touch
    # assignment at all until this point). One shared claim point either way.
    if not task.warehouse_order: return
    resource = my_resource(user)
    if not resource: return
    # State-guarded UPDATE (same idiom as pull_next_warehouse_order): of two scanners confirming tasks of one unassigned order, only the first claims it.
    frappe.db.sql("update `tabWarehouse Order` set assigned_resource=%s, status=if(status='Open', 'Assigned', status) where name=%s and (assigned_resource is null or assigned_resource='')",
        (resource.name, task.warehouse_order))
    if frappe.db.sql("select row_count()")[0][0]:
        frappe.db.set_value("Warehouse Task", {"warehouse_order": task.warehouse_order, "assigned_resource": ["in", ["", None]]}, "assigned_resource", resource.name)

# Stock sits under an HU from receiving on, so a task with a source HU is started by scanning that HU, never its bin.
# Exceptions: ad hoc moves/repack (Internal Move), Posting Change and counts, which work on bins.
BIN_SOURCE_TYPES = ("Internal Move", "Posting Change", "Inventory Count")

def _check_digits_bin(task, side):
    # Check digits replace a bin scan only when there's no competing HU to also verify on that
    # side (a Pick with both source_bin and source_hu, say) - mixed bin+HU confirmation keeps
    # today's "scan either" behavior unchanged; a pure-bin task (Putaway, Internal Move, Posting
    # Change) is exactly the case a browsed task list makes it easy to fake, since the bin name is
    # already sitting right there on screen. Returns the bin to validate against, or None if the
    # ordinary scan-matching below should run instead.
    bin_name = task.source_bin if side == "source" else task.destination_bin
    hu_name = task.source_hu if side == "source" else task.destination_hu
    if bin_name and not hu_name and frappe.db.get_single_value("WMS Settings", "require_bin_check_digits"):
        return bin_name
    return None

def verify_check_digits(bin_name, value):
    # Inline "wrong check digit, try again" feedback at the RF scan step - confirm_task (below)
    # is the real, authoritative check; this exists only so a mistyped code is caught immediately
    # instead of only surfacing when the whole task is confirmed.
    require_role("WMS Operator", "WMS Supervisor")
    actual = frappe.db.get_value("Storage Bin", bin_name, "check_digits")
    return bool(actual) and str(value or "").strip().upper() == actual.upper()

def confirm_task(task_name, scanned_source=None, scanned_destination=None, confirmed_quantity=None, destination_hu=None, device=None, idempotency_key=None, scanned_product=None, verify=True):
    # `verify` is deliberately not accepted over the whitelisted API (api/scanner.py's wrapper has
    # no such parameter) - it exists only for reverse_task below, a WMS Supervisor-only ledger
    # correction that confirms a system-generated compensating task, not an operator standing at a
    # bin. Any network caller always gets verify=True; there is no way to pass False from the wire.
    require_role("WMS Operator", "WMS Supervisor")
    # Locking read (for_update), not "select ... for update" then a plain get_doc: at Frappe's
    # REPEATABLE READ isolation that plain read returns the transaction's older snapshot, so a
    # second confirm arriving while the first holds the lock would read the task as it was
    # before the first one committed (status, confirmed_quantity and all). Same idiom at every
    # lock site in services/.
    task = frappe.get_doc("Warehouse Task", task_name, for_update=True)
    if task.status != "Confirmed":  # a lock is a lock: the RF and the desk both stop at what somebody else has open for change
        from frappe_wms.services.locks import require_free_many
        require_free_many([o for o in [("Warehouse Task", task.name), ("Warehouse Order", task.warehouse_order), ("Handling Unit", task.source_hu), ("Handling Unit", task.destination_hu)] if o[1]])
    if task.status == "Confirmed": return {"task": task.name, "status": task.status, "already_confirmed": True}
    if task.docstatus == 2 or task.status in {"Cancelled", "Exception"}: frappe.throw(_("Task is not confirmable"))
    if task.status == "On Hold": frappe.throw(task.blocking_reason or _("Task is on hold behind an earlier task in its Warehouse Order"))
    # Parents locked (task -> request -> order, always this order) before any status recompute below, so two confirms of one
    # request/order serialize and the second one sees the first one's committed task.
    if task.warehouse_request: frappe.db.get_value("Warehouse Request", task.warehouse_request, "name", for_update=True)
    if task.warehouse_order: frappe.db.get_value("Warehouse Order", task.warehouse_order, "name", for_update=True)
    _claim_warehouse_order_if_unassigned(task)
    if verify and frappe.db.get_single_value("WMS Settings", "require_scan_verification"):
        # Today scanned_source/scanned_destination are only checked when the caller bothers
        # to pass them - a caller (or a bypassed/scripted client) that omits them skips
        # verification entirely. This makes a scan mandatory for every field the task
        # actually has something to verify against, so the match checks below are
        # guaranteed to run rather than being skippable by omission.
        if (task.source_bin or task.source_hu) and not scanned_source: frappe.throw(_("Scan the source bin/HU before confirming"))
        if (task.destination_bin or task.destination_hu) and not scanned_destination: frappe.throw(_("Scan the destination bin/HU before confirming"))
        if task.product and not scanned_product: frappe.throw(_("Scan the product before confirming"))
    # Check digits are their own, independently-enabled anti-cheat setting (require_bin_check_digits)
    # - enforcing them must never be contingent on require_scan_verification above, nor skippable by
    # a caller that simply omits scanned_source/scanned_destination (reproduced: create_and_confirm_move
    # used to omit both unconditionally, which silently skipped this whole check for every ad-hoc
    # Internal Move even with the setting on - the "elif scanned_source and ..." shape below let a
    # falsy/omitted value through as if nothing needed verifying).
    src_check_bin = _check_digits_bin(task, "source") if verify else None
    if src_check_bin:
        if not scanned_source: frappe.throw(_("Enter the check digits for {0} before confirming").format(src_check_bin))
        if scanned_source.upper() != (frappe.db.get_value("Storage Bin", src_check_bin, "check_digits") or "").upper():
            frappe.throw(_("Check digits do not match {0}").format(src_check_bin))
    elif scanned_source and scanned_source not in {None if task.task_type not in BIN_SOURCE_TYPES and task.source_hu else task.source_bin, task.source_hu}:
        frappe.throw(_("Scanned source does not match the task"))
    dst_check_bin = _check_digits_bin(task, "destination") if verify else None
    if dst_check_bin:
        if not scanned_destination: frappe.throw(_("Enter the check digits for {0} before confirming").format(dst_check_bin))
        if scanned_destination.upper() != (frappe.db.get_value("Storage Bin", dst_check_bin, "check_digits") or "").upper():
            frappe.throw(_("Check digits do not match {0}").format(dst_check_bin))
    elif scanned_destination and scanned_destination not in {task.destination_bin, task.destination_hu}:
        frappe.throw(_("Scanned destination does not match the task"))
    if scanned_product and scanned_product != task.product: frappe.throw(_("Scanned product does not match the task"))
    already_confirmed = flt(task.confirmed_quantity)
    qty = flt(confirmed_quantity) if confirmed_quantity is not None else flt(task.planned_quantity) - already_confirmed
    if qty <= 0: frappe.throw(_("Invalid confirmed quantity"))
    # A resource confirming more than what's left on the plan (Unload/Putaway/Internal Move
    # finding genuinely more physical stock than expected) used to be a flat ValidationError -
    # there was no way to record what was actually found. The task itself can only ever
    # complete at its own planned_quantity; any excess is real stock that must be accounted for
    # right now, so it's posted straight to the warehouse's Difference Bin instead
    # (services/difference.record_over_difference), pending a supervisor deciding where it
    # actually belongs (clear_over_difference).
    excess = max(round(qty - (flt(task.planned_quantity) - already_confirmed), 6), 0)
    if excess > 0 and task.serial_no:
        # A serial number is exactly one unit - "extra" units found alongside it are different
        # serials that have to be received under their own numbers, not booked as more of this one.
        frappe.throw(_("Task {0} is for serial number {1} (1 unit) - receive any extra units with their own serial numbers").format(task.name, task.serial_no))
    posted_qty = qty - excess
    new_confirmed = already_confirmed + posted_qty
    if excess > 0:
        from frappe_wms.services.difference import difference_bin_for_warehouse
        difference_bin_for_warehouse(task.warehouse)  # fail loud before anything posts, not after
    if destination_hu == _UNPACK:
        resolved_destination_hu = None
    elif destination_hu and not frappe.db.exists("Handling Unit", destination_hu):
        # The operator may have scanned a brand-new tote/carton rather than an already-registered
        # one - reproduced under load: any task confirming less than a shared receiving tote's full
        # quantity into an HU-managed bin requires naming a destination HU (see
        # _resolve_partial_hu_move below), but there was no way to satisfy that with a fresh
        # barcode - frappe.get_doc in _relocate_hu_for_task below would just throw
        # DoesNotExistError, dead-ending the single most common split-putaway pattern (several
        # serials/units received onto one dock tote, each then confirmed into its own destination).
        # get_or_create_handling_unit already implements exactly this "scan a barcode that may or
        # may not exist yet" auto-registration for receiving; reuse it here instead of requiring
        # the destination to pre-exist. Only for a genuinely unregistered barcode, though - an
        # already-existing destination_hu (e.g. a whole HU moving with its own stock, or a cluster
        # tote a Pick already posted into) must resolve exactly as before: get_or_create_handling_unit's
        # "already has stock" guard exists to stop a *receipt* from double-posting into an occupied
        # HU, which does not apply to a task just relocating that same HU's own stock.
        from frappe_wms.services.handling_unit import get_or_create_handling_unit
        resolved_destination_hu = get_or_create_handling_unit(destination_hu, storage_bin=task.destination_bin, warehouse=task.warehouse)
    elif destination_hu:
        resolved_destination_hu = destination_hu
    elif task.unpack_at_destination:
        # The task itself was created knowing its destination is deliberately no HU (e.g. a
        # Deconsolidation line splitting stock loose into a bin) - not merely undecided, so this
        # must not fall back to task.source_hu the way an ordinary unspecified destination would
        # (reproduced: confirming such a task with the field left blank re-attached the split
        # stock to the very HU it was being deconsolidated out of, undoing the split entirely).
        resolved_destination_hu = None
    else:
        resolved_destination_hu = task.destination_hu or task.source_hu
    if resolved_destination_hu and task.destination_bin and hu_requirement(frappe.get_cached_doc("Storage Type", frappe.db.get_value("Storage Bin", task.destination_bin, "storage_type"))) == "Forbidden":
        resolved_destination_hu, destination_hu = None, _UNPACK  # Handling Units are not put into this storage type: the stock goes in loose
    source = {"warehouse": task.warehouse, "product": task.product, "batch_no": task.batch_no, "serial_no": task.serial_no, "handling_unit": task.source_hu, "storage_bin": task.source_bin, "stock_type": task.stock_type_from, "stock_uom": task.stock_uom}
    if any(task.get(k) for k in OWNER_KEYS): source.update(dim_values(task))  # else resolved from the stock itself
    destination = {"handling_unit": resolved_destination_hu, "storage_bin": task.destination_bin, "stock_type": task.stock_type_to or task.stock_type_from}
    key = idempotency_key or f"{task.idempotency_key or task.name}:{already_confirmed}"
    if frappe.db.exists("WMS Stock Ledger Entry", {"idempotency_key": f"{key}:1"}):
        # Same request retried after a lost response (flaky WiFi, reload mid-submit): it already posted and updated
        # the task, so answer with the current state instead of counting the quantity a second time.
        return {"task": task.name, "status": task.status, "quantity": qty, "released_tasks": [], "replayed": True}
    if posted_qty > 0 and resolved_destination_hu and resolved_destination_hu == task.source_hu and task.destination_bin != task.source_bin:
        resolved_destination_hu = _resolve_partial_hu_move(task, posted_qty)
        destination["handling_unit"] = resolved_destination_hu
        if resolved_destination_hu is None: destination_hu = _UNPACK
    if posted_qty > 0 and verify and task.source_hu and task.task_type != "Repack":
        from frappe_wms.services.handling_indicators import check_unpack
        check_unpack(task.product, task.source_hu, resolved_destination_hu)
    if posted_qty > 0 and resolved_destination_hu and resolved_destination_hu != task.source_hu and task.destination_bin and frappe.db.sql(
            "select 1 from `tabWMS Stock Balance` where handling_unit=%s and storage_bin!=%s and quantity>0 limit 1", (resolved_destination_hu, task.destination_bin)):
        # Relocating the destination HU would leave its existing stock behind: one HU in two bins.
        frappe.throw(_("Handling Unit {0} already holds stock in another bin - empty it or scan a different Handling Unit").format(resolved_destination_hu))
    if posted_qty > 0 and task.task_type == "Putaway" and task.destination_bin and task.destination_bin != task.source_bin \
            and not (resolved_destination_hu and frappe.db.get_value("Handling Unit", resolved_destination_hu, "current_bin") == task.destination_bin):
        # Capacity/blocked/mixing rules were checked when the bin was chosen at planning; re-checked (and the bin locked) now that stock lands in it,
        # since two receipts planned back to back can both have been given the same "first empty" bin.
        weight, volume = incoming_load(task.product, posted_qty)
        validate_destination_bin(task.destination_bin, item=task.product, incoming_quantity=posted_qty, stock_type=task.stock_type_to or task.stock_type_from,
            hu_type=frappe.db.get_value("Handling Unit", resolved_destination_hu, "hu_type") if resolved_destination_hu else None, batch_no=task.batch_no,
            destination_hu=resolved_destination_hu, incoming_weight=weight, incoming_volume=volume, incoming_hu_count=1 if resolved_destination_hu else 0, require_hu=False)
    if posted_qty > 0:
        transfer_stock(source=source, destination=destination, quantity=posted_qty, movement_type=task.movement_type, reference_doctype=task.doctype, reference_name=task.name, idempotency_key=key, warehouse_task=task.name, device=device)
    difference_name = None
    if excess > 0:
        from frappe_wms.services.difference import record_over_difference
        difference_name = record_over_difference(task, excess, f"{key}:diff")
    fully_confirmed = round(new_confirmed, 6) >= round(flt(task.planned_quantity), 6)
    status = "Confirmed" if fully_confirmed else "Partially Confirmed"
    updates = {"confirmed_quantity": new_confirmed, "status": status, "confirmed_at": now_datetime(), "confirmed_by": frappe.session.user, "confirmation_device": device, "idempotency_key": key, "destination_hu": resolved_destination_hu}
    if fully_confirmed: updates["docstatus"] = 1
    task.db_set(updates, update_modified=True)
    if fully_confirmed:
        advance_to_next_step(task)
        from frappe_wms.services.layout_control import advance
        advance(task)
    _update_request(task.warehouse_request)
    _update_allocations(task, posted_qty)
    if posted_qty > 0 and task.task_type == "Pick": _after_removal(task)
    from frappe_wms.services.production_supply import on_staging_confirmed
    on_staging_confirmed(task, posted_qty)
    if task.consolidation_group_line:
        from frappe_wms.services.consolidation import update_consolidation_progress
        update_consolidation_progress(task, posted_qty)
    # Not gated on fully_confirmed: transfer_stock above already moved this confirmation's
    # quantity in the ledger regardless of whether the task itself is done, so leaving the HU
    # record pointing at the old bin until the very last partial confirmation catches up would
    # make it lie about where its own just-confirmed stock actually is (reproduced in
    # production: a 100-unit Internal Move confirmed 50 at a time left Handling Unit.current_bin
    # frozen at the source bin after the first 50 had already ledger-moved to the destination -
    # the same HU then showed real stock at two different bins with no way to tell from the HU
    # record itself, which is what "the same HU in two different bins" surfaced as).
    _relocate_hu_for_task(task, destination_hu)
    if fully_confirmed and task.task_type == "Putaway":
        create_print_spool("Warehouse Task", task.name, "Putaway Confirmed", task.warehouse)
    if fully_confirmed and task.task_type == "Cross Dock":
        _apply_cross_dock_fulfillment(task)
    sort_task = None
    if fully_confirmed and task.task_type == "Pick" and task.requires_sort_after_pick:
        sort_task = _create_sort_task_after_pick(task)
    sync_warehouse_order(task.warehouse_order)
    released_tasks = release_next_in_sequence(task.warehouse_order) if fully_confirmed else []
    if fully_confirmed: released_tasks += _release_predecessor_gated_tasks(task.name)
    result = {"task": task.name, "status": status, "quantity": posted_qty, "released_tasks": released_tasks}
    if difference_name: result["difference"] = difference_name
    if sort_task: result["sort_task"] = sort_task
    return result

def _after_removal(task):
    """What a confirmed pick sets off when enabled in WMS Settings: replenishment of the pick bin, a zero stock count of an emptied bin."""
    replenish, zero_check = frappe.db.get_value("WMS Settings", "WMS Settings", ["replenish_on_removal", "zero_stock_check_on_pick"]) or (0, 0)
    if replenish and task.source_bin:
        from frappe_wms.services.replenishment import replenish_after_removal
        replenish_after_removal(task)
    if zero_check and task.source_bin and not flt(frappe.db.sql("select coalesce(sum(quantity), 0) from `tabWMS Stock Balance` where storage_bin=%s and quantity>0", task.source_bin)[0][0]):
        from frappe_wms.services.cycle_count import request_zero_stock_check
        request_zero_stock_check(task.warehouse, task.source_bin)

def _resolve_partial_hu_move(task, qty):
    # The destination fell back to "the same HU it came from" - right when the whole HU travels
    # (a full-pallet putaway, a whole-HU pick), but not when only part of its stock does: the
    # posting would tag the moved quantity to the source HU at the destination bin and
    # _relocate_hu_for_task would then move the HU record there too, while the rest of its stock
    # is still physically in the source bin - one HU "in" two bins. Reproduced under concurrent
    # picking: pallets picked from without a pick carton ended up with current_bin at the staging
    # lane and most of their stock still in the rack, invisible to anyone looking at the rack.
    # Returns the HU to post to: the source HU if it's being emptied, None (move the quantity
    # loose) if the destination bin doesn't manage HUs anyway; otherwise the operator has to say
    # which HU the partial quantity goes into.
    held = frappe.db.sql("select quantity from `tabWMS Stock Balance` where handling_unit=%s and storage_bin=%s for update",
        (task.source_hu, task.source_bin))
    remaining = sum(flt(r[0]) for r in held) - flt(qty)
    if remaining <= 0.000001:
        return task.source_hu
    storage_type = frappe.db.get_value("Storage Bin", task.destination_bin, "storage_type")
    # A Pick/Cross Dock must end in an HU: shipping finds the staged HU through the task's destination_hu.
    if task.task_type not in ("Pick", "Cross Dock") and hu_requirement(frappe.get_cached_doc("Storage Type", storage_type)) != "Mandatory":
        return None
    frappe.throw(_("Handling Unit {0} still holds {1} {2} in {3} after this - scan the Handling Unit (tote, carton or new pallet) you are putting these {4} into, or move the whole Handling Unit.").format(
        task.source_hu, frappe.format(remaining, "Float"), task.stock_uom or "", task.source_bin, frappe.format(qty, "Float")),
        title=_("Destination Handling Unit required"))

def _create_sort_task_after_pick(task):
    # Two-Step Picking's second hop: the Pick task's own destination was the warehouse's shared
    # picking staging area (see _pick_destination), not the delivery this stock is actually for -
    # a Sort task moves it on from there to the delivery's own staging bin. Not tied to the
    # original Stock Allocation (that was already fulfilled the moment the Pick task confirmed;
    # this is a pure physical relocation, no allocation/delivery-quantity bookkeeping to redo).
    outbound_delivery = frappe.db.get_value("Stock Allocation", task.stock_allocation, "outbound_delivery") if task.stock_allocation else None
    if not outbound_delivery: return None
    delivery_staging_bin = frappe.db.get_value("Outbound Delivery", outbound_delivery, "staging_bin")
    if not delivery_staging_bin: return None
    process_type_name = determine_process_type(task.warehouse, "Sort", item=task.product, stock_type=task.stock_type_to or task.stock_type_from, default="OB_SORT")
    process_type = frappe.get_cached_doc("Warehouse Process Type", process_type_name)
    sort_task = frappe.get_doc({
        "doctype": "Warehouse Task", "task_type": "Sort", "warehouse": task.warehouse, "product": task.product,
        "planned_quantity": task.confirmed_quantity, "stock_uom": task.stock_uom, "batch_no": task.batch_no, "serial_no": task.serial_no,
        "source_bin": task.destination_bin, "destination_bin": delivery_staging_bin,
        "stock_type_from": task.stock_type_to or task.stock_type_from, "stock_type_to": task.stock_type_to or task.stock_type_from,
        "movement_type": process_type.movement_type, "priority": task.priority or "Normal", "status": "Open",
    })
    attach_task(sort_task, frappe.generate_hash(length=10), reference_doctype="Outbound Delivery", reference_name=outbound_delivery)
    sort_task.insert(ignore_permissions=True)
    return sort_task.name

def _apply_cross_dock_fulfillment(task):
    # Cross-docked stock is staged directly and never sits in an allocatable bin (Staging is
    # a NON_ALLOCATABLE_STORAGE_ROLE), so it deliberately bypasses Stock Allocation/
    # allocate_delivery/the normal pick pipeline entirely - normal allocation could never have
    # found it there anyway. This directly satisfies the matched delivery line instead.
    if not task.warehouse_request: return
    request = frappe.db.get_value("Warehouse Request", task.warehouse_request, ["reference_doctype", "reference_name", "reference_line"], as_dict=True)
    if not request or request.reference_doctype != "Outbound Delivery" or not request.reference_line: return
    # The line was already reserved (allocated_quantity) when the Cross Dock request was raised -
    # see cross_dock.reserve_cross_dock_demand - so only picked_quantity moves here. max() keeps a
    # request raised before that reservation existed covered without double-counting a new one.
    line = frappe.db.get_value("Outbound Delivery Item", request.reference_line, ["allocated_quantity", "picked_quantity"], as_dict=True, for_update=True)
    picked = flt(line.picked_quantity) + flt(task.confirmed_quantity)
    frappe.db.set_value("Outbound Delivery Item", request.reference_line, {
        "allocated_quantity": max(flt(line.allocated_quantity), picked),
        "picked_quantity": picked,
    })
    _update_delivery_picking_status(request.reference_name)

def _release_predecessor_gated_tasks(task_name):
    # A second, independent hold: unlike release_next_in_sequence (same Warehouse Order only),
    # this can release a task sitting in a completely different Warehouse Order/queue - the
    # normal case for a chained Storage Process step, whose next step is usually a different
    # activity and therefore a different queue entirely.
    blocked = frappe.get_all("Warehouse Task", filters={"predecessor_task": task_name, "status": "On Hold", "docstatus": ["<", 2]},
        fields=["name", "warehouse_order", "sequence"])
    released = []
    for row in blocked:
        if _sequence_gate_blocks(row): continue
        resource = frappe.db.get_value("Warehouse Order", row.warehouse_order, "assigned_resource") if row.warehouse_order else None
        new_status = "Assigned" if resource else "Open"
        frappe.db.set_value("Warehouse Task", row.name, {"status": new_status, "blocking_reason": None}, update_modified=True)
        released.append(row.name)
    return released

def _update_allocations(task, qty):
    rows = task.get("stock_allocations") or ([frappe._dict(stock_allocation=task.stock_allocation, allocated_quantity=qty)] if task.stock_allocation else [])
    if not rows: return
    release_allocation({"warehouse": task.warehouse, "product": task.product, "batch_no": task.batch_no, "serial_no": task.serial_no, "handling_unit": task.source_hu, "storage_bin": task.source_bin, "stock_type": task.stock_type_from,
        **dim_values(task)}, qty)
    remaining = qty
    for row in rows:
        if remaining <= 0: break
        allocation = frappe.get_doc("Stock Allocation", row.stock_allocation, for_update=True)
        outstanding = flt(allocation.allocated_quantity) - flt(allocation.picked_quantity)
        if outstanding <= 0: continue
        take = min(remaining, outstanding)
        picked = flt(allocation.picked_quantity) + take
        status = "Picked" if picked >= flt(allocation.allocated_quantity) else "Partially Picked"
        frappe.db.set_value("Stock Allocation", allocation.name, {"picked_quantity": picked, "status": status})
        if row.get("name"):
            frappe.db.set_value("Warehouse Task Allocation", row.name, "picked_quantity", flt(row.picked_quantity) + take)
        if allocation.outbound_delivery_item:
            # Atomic delta: two picks of one delivery line must both count.
            frappe.db.sql("update `tabOutbound Delivery Item` set picked_quantity=picked_quantity+%s where name=%s", (take, allocation.outbound_delivery_item))
        _update_delivery_picking_status(allocation.outbound_delivery)
        remaining -= take

def _update_delivery_picking_status(delivery_name):
    if not delivery_name: return
    # Locking read: a plain one would see this transaction's older snapshot and miss a concurrent pick of another line.
    rows = frappe.db.sql("select name, requested_quantity, picked_quantity from `tabOutbound Delivery Item` where parent=%s for update", delivery_name, as_dict=True)
    if not rows: return
    fully_picked = all(flt(r.picked_quantity) >= flt(r.requested_quantity) for r in rows)
    any_picked = any(flt(r.picked_quantity) > 0 for r in rows)
    if fully_picked:
        picking_status = "Picked"
    elif any_picked:
        picking_status = "Partially Picked"
    else:
        # Nothing picked yet - but if every line's outstanding quantity is already reserved
        # against an open Cross Dock request, no Pick task will ever be created for any of it
        # (see _apply_cross_dock_fulfillment below): "Not Started" would wrongly read as "pick
        # work is pending" when there is none - something else (cross-docking, straight off the
        # receiving dock) is delivering it instead. Reproduced live: 15 production deliveries
        # sat at "Not Started" indefinitely with zero Pick tasks ever created for them, with
        # nothing in the Outbound Monitor to explain why.
        cross_dock_qty = {}
        for wr in frappe.get_all("Warehouse Request", filters={"request_type": "Cross Dock", "reference_doctype": "Outbound Delivery",
                "reference_name": delivery_name, "status": ["!=", "Cancelled"]}, fields=["reference_line", "requested_quantity"]):
            cross_dock_qty[wr.reference_line] = cross_dock_qty.get(wr.reference_line, 0) + flt(wr.requested_quantity)
        if cross_dock_qty and all(flt(r.requested_quantity) - flt(r.picked_quantity) <= cross_dock_qty.get(r.name, 0) + 0.000001 for r in rows):
            picking_status = "Not Relevant"
        else:
            picking_status = "Not Started"
    values = {"picking_status": picking_status}
    if fully_picked: values["status"] = "Picked"
    frappe.db.set_value("Outbound Delivery", delivery_name, values)

def _update_request(name):
    if not name: return
    # created = what the request asked to move: a follow-up leg (storage process step, intermediate bin) moves the same goods again
    totals = frappe.db.sql("select coalesce(sum(case when ifnull(predecessor_task, '') = '' then planned_quantity end),0), coalesce(sum(case when ifnull(predecessor_task, '') = '' then confirmed_quantity end),0), count(*), sum(status='Confirmed') from `tabWarehouse Task` where warehouse_request=%s and docstatus<2 for update", name)[0]
    status = "Completed" if totals[2] and totals[2] == totals[3] else "In Process"
    frappe.db.set_value("Warehouse Request", name, {"created_quantity": totals[0], "confirmed_quantity": totals[1], "status": status})
    if status == "Completed":
        request = frappe.get_doc("Warehouse Request", name)
        if request.reference_doctype == "Work Order":
            from frappe_wms.services.erp_sync_queue import dispatch
            dispatch("work_order_transfer", request)

def _relocate_hu_for_task(task, destination_hu=None):
    # An explicit "no HU" (see _UNPACK) means only the stock moved, not a container - the source
    # HU (which may still hold whatever of its balance wasn't just unpacked) must stay put. Same
    # for a task created with unpack_at_destination set, unless the operator scanned a real HU
    # at confirm time anyway (that still wins).
    if destination_hu == _UNPACK: return
    hu = destination_hu or task.destination_hu or (None if task.unpack_at_destination else task.source_hu)
    if not hu or not task.destination_bin: return
    if task.move_top_hu:
        hu = frappe.db.get_value("Handling Unit", hu, "top_hu") or hu
    _move_hu_and_descendants(hu, task.destination_bin, task.source_bin, task, top_level=True)

def _move_hu_and_descendants(hu, destination_bin, source_bin, task, top_level):
    doc = frappe.get_doc("Handling Unit", hu)
    if doc.current_bin == destination_bin and not (top_level and doc.parent_hu):
        # Already there - e.g. an empty pick carton the operator created right at the staging
        # lane before scanning it as the destination. It still has to become Staged once stock is
        # picked into it, or it never shows up as staged/shippable.
        if top_level and task.task_type in ("Stage", "Pick", "Cross Dock") and not task.reversal_of and doc.status != "Staged":
            doc.flags.wms_service_update = True
            doc.status = "Staged"
            doc.save(ignore_permissions=True)
        return
    bin_before = doc.current_bin
    doc.flags.wms_service_update = True
    doc.current_bin = destination_bin
    if top_level: doc.status = "Staged" if task.task_type in ("Stage", "Pick", "Cross Dock") and not task.reversal_of else doc.status
    if top_level and doc.parent_hu:
        # The HU this task is relocating was nested inside a parent (e.g. Repacked into a tote)
        # that isn't part of this move - relocating it while it stays a child would leave parent
        # and child in different bins, which validate_hu correctly rejects. move_top_hu (handled
        # by the caller, _relocate_hu_for_task) already resolves up to the real top-level
        # ancestor first when the whole container is meant to travel together; reaching here with
        # a parent still set means this HU is being extracted from that container, not moved
        # along with it, so it leaves the hierarchy the same way a real picker physically lifting
        # it out of the tote would. Reproduced live: picking stock out of a Repacked HU threw
        # "Parent and child handling units must be in the same warehouse and bin".
        doc.parent_hu = None
    doc.save(ignore_permissions=True)
    frappe.get_doc({"doctype": "Handling Unit Event", "handling_unit": hu, "event_type": "Moved", "bin_before": bin_before or source_bin, "bin_after": destination_bin, "warehouse_task": task.name, "event_timestamp": now_datetime(), "performed_by": frappe.session.user}).insert(ignore_permissions=True)
    for child in frappe.get_all("Handling Unit", filters={"parent_hu": hu}, pluck="name"):
        _move_hu_and_descendants(child, destination_bin, source_bin, task, top_level=False)

def _unwind_pick_task_allocations(task, qty):
    # A reversed Pick task's stock is physically back at the source - the Stock Allocation and
    # Outbound Delivery Item it fed must revert with it, or the delivery is stuck reporting more
    # picked than is actually sitting in any Handling Unit (reproduced by reading the reversal
    # path against _ready_lines_for_delivery: nothing re-enables a fresh pick, and Goods Issue
    # could be attempted against a delivery whose "picked" stock had already moved back to the
    # shelf).
    rows = task.get("stock_allocations") or ([frappe._dict(stock_allocation=task.stock_allocation)] if task.stock_allocation else [])
    if not rows: return
    delivery_names = set()
    remaining = qty
    for row in rows:
        if remaining <= 0: break
        allocation = frappe.get_doc("Stock Allocation", row.stock_allocation, for_update=True)
        take = min(remaining, flt(allocation.picked_quantity))
        if take <= 0: continue
        new_picked = flt(allocation.picked_quantity) - take
        status = "Picked" if new_picked > 0 and new_picked >= flt(allocation.allocated_quantity) else ("Partially Picked" if new_picked > 0 else "Allocated")
        frappe.db.set_value("Stock Allocation", allocation.name, {"picked_quantity": new_picked, "status": status})
        if allocation.outbound_delivery_item:
            frappe.db.sql("update `tabOutbound Delivery Item` set picked_quantity=greatest(picked_quantity-%s, 0) where name=%s", (take, allocation.outbound_delivery_item))
        if allocation.outbound_delivery: delivery_names.add(allocation.outbound_delivery)
        remaining -= take
    for delivery_name in delivery_names:
        _update_delivery_picking_status(delivery_name)
        current_status, picking_status = frappe.db.get_value("Outbound Delivery", delivery_name, ["status", "picking_status"])
        if current_status == "Picked" and picking_status != "Picked":
            frappe.db.set_value("Outbound Delivery", delivery_name, "status", "Picking" if picking_status != "Not Started" else "Allocated")

def reverse_task(task_name, reason=None):
    # Creates and confirms a compensating task that moves the confirmed quantity back from
    # destination to source, rather than un-confirming the original (the stock ledger is
    # immutable, mirroring how goods receipt/issue reversals work). This corrects the physical
    # stock position; for a Pick task it also unwinds the Stock Allocation/Outbound Delivery
    # status the original confirmation advanced (_unwind_pick_task_allocations) - otherwise the
    # delivery keeps reporting stock as picked that has actually moved back to the shelf.
    require_role("WMS Supervisor")
    original = frappe.get_doc("Warehouse Task", task_name, for_update=True)
    if original.docstatus != 1 or original.status != "Confirmed":
        frappe.throw(_("Only a fully confirmed task can be reversed"))
    if frappe.db.exists("Warehouse Task", {"reversal_of": original.name}):
        frappe.throw(_("Task has already been reversed"))
    qty = flt(original.confirmed_quantity)
    reversal = frappe.get_doc({
        "doctype": "Warehouse Task", "task_type": original.task_type, "warehouse": original.warehouse,
        "product": original.product, "planned_quantity": qty, "stock_uom": original.stock_uom,
        "batch_no": original.batch_no, "serial_no": original.serial_no,
        "source_bin": original.destination_bin, "destination_bin": original.source_bin,
        "source_hu": original.destination_hu or original.source_hu, "destination_hu": original.source_hu,
        "stock_type_from": original.stock_type_to or original.stock_type_from, "stock_type_to": original.stock_type_from,
        "movement_type": original.movement_type, "priority": original.priority, "status": "Open",
        "reversal_of": original.name, "idempotency_key": f"{original.idempotency_key or original.name}:reversal",
    })
    reversal.insert(ignore_permissions=True)
    # A supervisor correcting the ledger, not an operator standing at a bin - nothing to verify a
    # scan or check digits against here, so this is the one caller allowed to skip it.
    result = confirm_task(reversal.name, confirmed_quantity=qty, verify=False)
    if original.task_type == "Pick": _unwind_pick_task_allocations(original, qty)
    frappe.db.set_value("Warehouse Task", original.name, "blocking_reason", reason or _("Reversed by {0}").format(reversal.name))
    return {"original": original.name, "reversal": reversal.name, "status": result["status"]}
