import json
import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate
from frappe_wms.services.removal_rules import apply_strategy, match_removal_rule, strategy_fefo
from frappe_wms.services.task import task_names_for_allocations
from frappe_wms.services.warehouse_order import release_next_in_sequence, sync_warehouse_order
from frappe_wms.utils import require_role

# Storage roles that don't hold general on-hand inventory available for a *new* outbound
# allocation: Receiving is unputaway stock still awaiting putaway, Staging/Shipping/Door hold
# stock already committed to (picked or loaded for) a specific outbound movement, Packing is
# mid-repack. None of that should be up for grabs by a delivery's FIFO allocation just because
# the ledger still shows it as "available" there - nothing else marks staged/received stock as
# reserved or in-transit once it physically arrives at that bin.
NON_ALLOCATABLE_STORAGE_ROLES = ("Receiving", "Staging", "Shipping", "Door", "Packing")

# A balance row's own storage-role/bin-flag exclusion above says nothing about the Handling
# Unit it's tied to - a Blocked HU (an operator's own "don't touch this" flag, e.g. Repack
# Center's Block button) or one already Cancelled/Shipped/Loaded for a different movement was
# still being freely allocated to a brand new delivery (reproduced by reading the code: nothing
# here ever queried Handling Unit.status at all).
NON_ALLOCATABLE_HU_STATUSES = ("Blocked", "Cancelled", "Shipped", "Loaded")

def _matching_batches_for_characteristics(requirements):
    # SAP EWM batch determination, scoped down: an exact-match filter, not a full classification
    # system with value ranges/tolerances. A batch qualifies only if it has EVERY required
    # characteristic at EXACTLY the required value - one query per requirement, intersected,
    # since "has all of N (characteristic, value) pairs" isn't expressible as a single filter
    # against a table keyed one row per (batch, characteristic).
    matching = None
    for characteristic, value in requirements.items():
        batches = set(frappe.get_all("WMS Batch Characteristic Value",
            filters={"characteristic": characteristic, "value": value}, pluck="batch_no"))
        matching = batches if matching is None else (matching & batches)
        if not matching: break
    return matching or set()

def _required_characteristics(row):
    # A JSON string on the row, not a Table field - Outbound Delivery Item is itself a child
    # doctype, and Frappe never reloads a child row's own nested child table once its parent
    # (Outbound Delivery) is the thing being loaded (confirmed empirically: it round-trips fine
    # in memory before the first save, but comes back empty after insert+reload).
    raw = row.get("required_characteristics")
    return json.loads(raw) if raw else {}

def _candidate_balances(row, warehouse):
    filters={"warehouse":warehouse,"product":row.item,"stock_type":row.required_stock_type,"available_quantity":[">",0]}
    if row.required_serial_no: filters["serial_no"]=row.required_serial_no
    requirements = _required_characteristics(row)
    if requirements:
        matching_batches = _matching_batches_for_characteristics(requirements)
        if row.required_batch: matching_batches &= {row.required_batch}
        if not matching_batches: return []
        filters["batch_no"] = ["in", list(matching_batches)]
    elif row.required_batch:
        filters["batch_no"] = row.required_batch
    balances = frappe.get_all("WMS Stock Balance",filters=filters,fields=["*"],order_by="first_receipt_date asc, name asc")

    item_group = frappe.db.get_value("Item", row.item, "item_group")
    rule = match_removal_rule(warehouse, item=row.item, item_group=item_group, stock_type=row.required_stock_type)
    if rule:
        balances = apply_strategy(rule.strategy, balances, sort_fields=rule.sort_fields, fixed_bin=rule.fixed_bin)
    else:
        # No configured Removal Rule for this warehouse/item - preserve the exact FEFO-then-FIFO
        # default every warehouse had before Removal Rules existed (soonest expiry first, nulls
        # last, FIFO among ties/unset expiries).
        balances = strategy_fefo(balances, {})

    min_remaining = frappe.db.get_value("WMS Product", row.item, "minimum_remaining_shelf_life")
    if min_remaining:
        cutoff = add_days(nowdate(), min_remaining)
        balances = [b for b in balances if not b.shelf_life_expiry_date or getdate(b.shelf_life_expiry_date) >= getdate(cutoff)]

    hu_names = {b.handling_unit for b in balances if b.handling_unit}
    if hu_names:
        non_allocatable_hus = set(frappe.get_all("Handling Unit", filters={"name": ["in", list(hu_names)], "status": ["in", NON_ALLOCATABLE_HU_STATUSES]}, pluck="name"))
        balances = [b for b in balances if not b.handling_unit or b.handling_unit not in non_allocatable_hus]

    bin_names = {b.storage_bin for b in balances if b.storage_bin}
    if not bin_names: return balances
    non_allocatable_bins = set(frappe.get_all("Storage Bin", filters={"name": ["in", list(bin_names)], "removal_blocked": 1}, pluck="name"))
    blocked_types = frappe.get_all("Storage Type", filters={"warehouse": warehouse, "storage_role": ["in", NON_ALLOCATABLE_STORAGE_ROLES]}, pluck="name")
    if blocked_types:
        non_allocatable_bins |= set(frappe.get_all("Storage Bin", filters={"name": ["in", list(bin_names)], "storage_type": ["in", blocked_types]}, pluck="name"))
    return [b for b in balances if not b.storage_bin or b.storage_bin not in non_allocatable_bins]

def allocate_delivery(delivery_name):
    require_role("WMS Operator", "WMS Picker", "WMS Supervisor")
    # for_update: serializes concurrent allocation of the same delivery and makes the
    # allocated_quantity read below current rather than a REPEATABLE READ snapshot.
    doc=frappe.get_doc("Outbound Delivery",delivery_name,for_update=True); doc.check_permission("write")
    if doc.docstatus != 1: frappe.throw(_("Outbound Delivery must be submitted before it can be allocated"))
    created=[]
    for row in doc.items:
        needed=flt(row.requested_quantity)-flt(row.allocated_quantity)
        for stock in _candidate_balances(row,doc.warehouse):
            if needed<=0: break
            # One locking read, not "lock, then plain get_value": at REPEATABLE READ the plain read
            # returns this transaction's older snapshot, so two deliveries allocating the same
            # product at once could both see (and both reserve) the same available quantity.
            fresh = frappe.db.get_value("WMS Stock Balance", stock.name, ["available_quantity", "allocated_quantity"], as_dict=True, for_update=True)
            if not fresh or flt(fresh.available_quantity) <= 0: continue
            qty=min(needed,flt(fresh.available_quantity))
            allocation=frappe.get_doc({"doctype":"Stock Allocation","outbound_delivery":doc.name,"outbound_delivery_item":row.name,"product":row.item,"stock_balance":stock.name,"storage_bin":stock.storage_bin,"handling_unit":stock.handling_unit,"batch_no":stock.batch_no,"serial_no":stock.serial_no,"stock_type":stock.stock_type,"allocated_quantity":qty,"status":"Allocated"})
            # Stock Allocation is an internal bookkeeping record this function creates as a side
            # effect of an already-role-gated action (require_role above) - same as every other
            # WMS-internal doctype insert across the codebase (Warehouse Request, Warehouse Task,
            # Goods Receipt, ...), it doesn't need its own separate doctype permission on top of
            # that. Missing this was a real production bug: a WMS Supervisor/Picker with no
            # System Manager/WMS Administrator role got a PermissionError here.
            allocation.insert(ignore_permissions=True); created.append(allocation.name)
            frappe.db.set_value("WMS Stock Balance",stock.name,{"allocated_quantity":flt(fresh.allocated_quantity)+qty,"available_quantity":flt(fresh.available_quantity)-qty})
            needed-=qty
        row.db_set("allocated_quantity",flt(row.requested_quantity)-needed)
    # "Not Allocated" when nothing at all could be reserved - it used to fall through to "Partially
    # Allocated", so release_delivery_for_picking's own "No stock could be allocated" check never
    # fired and the operator got a confusing "No open allocations to create pick tasks for".
    if all(flt(x.allocated_quantity)>=flt(x.requested_quantity) for x in doc.items): status = "Fully Allocated"
    elif any(flt(x.allocated_quantity)>0 for x in doc.items): status = "Partially Allocated"
    else: status = "Not Allocated"
    doc.db_set("allocation_status", status)
    return created

def cancel_allocations_for_delivery(delivery_name):
    # Unwinds whatever an Outbound Delivery's cancellation can still safely undo: releases each
    # Stock Allocation's reservation back onto its WMS Stock Balance and hard-cancels any
    # still-open (unconfirmed) Pick task referencing it. events.deliveries.before_cancel_outbound_delivery
    # already guarantees nothing here has been physically picked yet.
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": delivery_name, "status": ["!=", "Cancelled"]},
        fields=["name", "stock_balance", "allocated_quantity"])
    if not allocations: return
    task_names = task_names_for_allocations([a.name for a in allocations])
    warehouse_orders = set()
    for task_name in task_names:
        task = frappe.db.get_value("Warehouse Task", task_name, ["docstatus", "warehouse_order"], as_dict=True)
        if task and task.docstatus == 0:
            frappe.db.set_value("Warehouse Task", task_name, {"status": "Cancelled", "docstatus": 2}, update_modified=True)
            if task.warehouse_order: warehouse_orders.add(task.warehouse_order)
    for wo_name in warehouse_orders:
        release_next_in_sequence(wo_name)
        sync_warehouse_order(wo_name)
    for allocation in allocations:
        if allocation.stock_balance and frappe.db.exists("WMS Stock Balance", allocation.stock_balance):
            balance = frappe.get_doc("WMS Stock Balance", allocation.stock_balance, for_update=True)
            balance.allocated_quantity = max(flt(balance.allocated_quantity) - flt(allocation.allocated_quantity), 0)
            balance.available_quantity = flt(balance.quantity) - balance.allocated_quantity
            balance.flags.ignore_permissions = True
            balance.save()
        frappe.db.set_value("Stock Allocation", allocation.name, "status", "Cancelled")
    from frappe_wms.services.consolidation import cancel_consolidation_lines_for_allocations
    cancel_consolidation_lines_for_allocations([a.name for a in allocations])
