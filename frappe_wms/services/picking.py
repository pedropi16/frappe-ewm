import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.allocation import allocate_delivery
from frappe_wms.services.task import create_pick_tasks, create_pick_tasks_for_wave, my_resource, OPEN_TASK_STATUSES
from frappe_wms.utils import require_role

OPEN_RELEASE_STATUSES = ("Draft", "Open", "Allocated")

PICK_TASK_FIELDS = ["name", "task_type", "warehouse", "product", "planned_quantity", "confirmed_quantity",
    "stock_uom", "source_bin", "destination_bin", "source_hu", "destination_hu",
    "priority", "status", "movement_type", "sequence", "queue", "wave", "warehouse_order", "assigned_resource",
    "blocking_reason"]

def find_pick_tasks(reference):
    # SAP EWM-style picking entry points: jump straight into the pick-task wizard by scanning
    # the Warehouse Order or Outbound Delivery it belongs to, or a Handling Unit involved in it -
    # rather than only browsing the full "Pick Tasks" list.
    require_role("WMS Operator", "WMS Picker", "WMS Supervisor")
    reference = (reference or "").strip()
    if not reference:
        frappe.throw(_("Scan or enter a Warehouse Order, Outbound Delivery, or Handling Unit"))
    resource = my_resource()
    base_filters = {"task_type": "Pick", "status": ["in", OPEN_TASK_STATUSES], "docstatus": 0}
    if resource: base_filters["warehouse"] = resource.warehouse

    if frappe.db.exists("Warehouse Order", reference):
        tasks = frappe.get_all("Warehouse Task", filters={**base_filters, "warehouse_order": reference}, fields=PICK_TASK_FIELDS)
    elif frappe.db.exists("Outbound Delivery", reference):
        allocation_names = frappe.get_all("Stock Allocation", filters={"outbound_delivery": reference}, pluck="name")
        if not allocation_names: return []
        cluster_task_names = frappe.get_all("Warehouse Task Allocation", filters={"stock_allocation": ["in", allocation_names]}, pluck="parent")
        direct_task_names = frappe.get_all("Warehouse Task", filters={"stock_allocation": ["in", allocation_names]}, pluck="name")
        task_names = list(set(cluster_task_names) | set(direct_task_names))
        tasks = frappe.get_all("Warehouse Task", filters={**base_filters, "name": ["in", task_names]}, fields=PICK_TASK_FIELDS) if task_names else []
    else:
        by_source = frappe.get_all("Warehouse Task", filters={**base_filters, "source_hu": reference}, fields=PICK_TASK_FIELDS)
        by_dest = frappe.get_all("Warehouse Task", filters={**base_filters, "destination_hu": reference}, fields=PICK_TASK_FIELDS)
        seen = {t.name for t in by_source}
        tasks = by_source + [t for t in by_dest if t.name not in seen]
    return sorted(tasks, key=lambda t: (t.sequence or 0, t.name))

def release_wave(wave_name):
    require_role("WMS Supervisor")
    frappe.db.sql("select name from `tabWMS Wave` where name=%s for update", wave_name)
    wave = frappe.get_doc("WMS Wave", wave_name)
    if wave.status != "Draft": frappe.throw(_("Wave is not in Draft status"))
    if not wave.deliveries: frappe.throw(_("Wave has no deliveries to release"))
    delivery_names = [row.outbound_delivery for row in wave.deliveries]
    for delivery_name in delivery_names:
        delivery = frappe.get_doc("Outbound Delivery", delivery_name)
        if delivery.allocation_status != "Fully Allocated":
            allocate_delivery(delivery_name)
    created = create_pick_tasks_for_wave(delivery_names, wave.picking_strategy, wave=wave.name)
    wave.db_set({"status": "Released", "released_at": now_datetime(), "released_by": frappe.session.user}, update_modified=True)
    return created

def list_awaiting_release(user=None):
    # Deliveries that still need allocation and/or pick-task creation - the RF "Release"
    # screen's list, filling the gap between a delivery existing and its Pick Tasks showing up
    # in the Tasks screen.
    require_role("WMS Operator", "WMS Picker", "WMS Supervisor")
    resource = my_resource(user)
    filters = {"status": ["in", OPEN_RELEASE_STATUSES]}
    if resource: filters["warehouse"] = resource.warehouse
    deliveries = frappe.get_list("Outbound Delivery", filters=filters,
        fields=["name", "outbound_delivery_number", "warehouse", "customer", "staging_bin",
            "status", "allocation_status", "picking_status", "delivery_date"],
        order_by="delivery_date asc, creation asc", limit=50)
    for delivery in deliveries:
        rows = frappe.get_all("Outbound Delivery Item", filters={"parent": delivery.name},
            fields=["item", "requested_quantity", "allocated_quantity"])
        delivery["line_count"] = len(rows)
        delivery["fully_allocated"] = bool(rows) and all(flt(r.allocated_quantity) >= flt(r.requested_quantity) for r in rows)
    return deliveries

def release_delivery_for_picking(delivery_name, strategy="Single Order"):
    # One RF tap instead of the two-step allocate-then-create-pick-tasks desk flow
    # (Outbound Delivery's "Allocate Stock" / "Create Pick Tasks" buttons) - allocates whatever
    # isn't already allocated, then raises pick tasks for it.
    require_role("WMS Operator", "WMS Picker", "WMS Supervisor")
    frappe.db.sql("select name from `tabOutbound Delivery` where name=%s for update", delivery_name)
    delivery = frappe.get_doc("Outbound Delivery", delivery_name)
    if delivery.picking_status == "Picked": frappe.throw(_("Delivery is already fully picked"))
    if delivery.allocation_status != "Fully Allocated":
        allocate_delivery(delivery_name)
        delivery.reload()
    if delivery.allocation_status == "Not Allocated":
        frappe.throw(_("No stock could be allocated for this delivery - check available balances"))
    return create_pick_tasks(delivery_name, strategy)
