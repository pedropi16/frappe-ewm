import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.allocation import allocate_delivery
from frappe_wms.services.task import create_pick_tasks_for_wave

def release_wave(wave_name):
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
