import frappe
from datetime import datetime
from frappe.utils import nowdate, now_datetime, get_time, getdate
from frappe_wms.services.picking import release_wave

OPEN_WAVE_STATUSES = ("Draft", "Released", "Picking", "Picked")

def _already_waved(delivery_names):
    if not delivery_names: return set()
    rows = frappe.db.sql("""
        select wd.outbound_delivery
        from `tabWMS Wave Delivery` wd
        join `tabWMS Wave` w on w.name = wd.parent
        where wd.outbound_delivery in %(names)s and w.status in %(open_statuses)s
    """, {"names": tuple(delivery_names), "open_statuses": OPEN_WAVE_STATUSES})
    return {r[0] for r in rows}

def generate_waves_from_templates():
    # One new Draft wave per template per run, skipped entirely when there's nothing new to
    # sweep - a template with no matching (and not-already-waved) deliveries today creates
    # nothing, so repeated daily runs don't produce empty waves.
    created = []
    for template in frappe.get_all("Wave Template", filters={"active": 1}, fields=["*"]):
        # Outbound Delivery.status stays "Draft" throughout the whole pre-pick lifecycle in
        # this codebase - nothing sets it to "Open"/"Allocated" anywhere; "Picking" is the
        # first real transition, made by create_pick_tasks_for_wave once a wave releases.
        # So "Draft" (not yet on any wave) is the correct scope for a fresh sweep.
        filters = {"warehouse": template.warehouse, "docstatus": 1, "status": "Draft", "delivery_date": ["<=", nowdate()]}
        if template.route: filters["route"] = template.route
        deliveries = frappe.get_all("Outbound Delivery", filters=filters, pluck="name")
        if not deliveries: continue
        already = _already_waved(deliveries)
        matches = [d for d in deliveries if d not in already]
        if not matches: continue
        wave = frappe.get_doc({
            "doctype": "WMS Wave", "warehouse": template.warehouse, "route": template.route,
            "priority": template.priority, "picking_strategy": template.picking_strategy,
            "wave_template": template.name, "status": "Draft",
            "deliveries": [{"outbound_delivery": d} for d in matches],
        })
        wave.insert(ignore_permissions=True)
        created.append(wave.name)
    return created

def auto_release_due_waves():
    released = []
    for wave in frappe.get_all("WMS Wave", filters={"status": "Draft", "wave_template": ["is", "set"]}, fields=["name", "wave_template"]):
        template = frappe.db.get_value("Wave Template", wave.wave_template, ["auto_release", "cutoff_time"], as_dict=True)
        if not template or not template.auto_release or not template.cutoff_time: continue
        cutoff = datetime.combine(getdate(nowdate()), get_time(template.cutoff_time))
        if now_datetime() < cutoff: continue
        released.append(release_wave(wave.name))
    return released
