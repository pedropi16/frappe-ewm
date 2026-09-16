import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.allocation import allocate_delivery
from frappe_wms.services.task import create_pick_tasks, create_pick_tasks_for_wave, my_resource
from frappe_wms.utils import require_role

OPEN_RELEASE_STATUSES = ("Draft", "Open", "Allocated")

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
