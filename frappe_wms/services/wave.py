import frappe
from datetime import datetime
from frappe import _
from frappe.utils import nowdate, now_datetime, get_time, getdate, add_to_date
from frappe_wms.utils import require_role
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
        # Outbound Delivery.status is "Open" (after submit) throughout the whole pre-pick lifecycle in
        # this codebase - nothing sets it to "Allocated" anywhere; "Picking" is the
        # first real transition, made by create_pick_tasks_for_wave once a wave releases.
        # So "Open" (not yet on any wave) is the correct scope for a fresh sweep.
        filters = {"warehouse": template.warehouse, "docstatus": 1, "status": ["in", ["Draft", "Open"]], "delivery_date": ["<=", nowdate()]}
        if template.route: filters["route"] = template.route
        deliveries = frappe.get_all("Outbound Delivery", filters=filters, pluck="name")
        if not deliveries: continue
        already = _already_waved(deliveries)
        matches = [d for d in deliveries if d not in already]
        if not matches: continue
        size = template.maximum_deliveries or len(matches)
        for start in range(0, len(matches), size):
            wave = frappe.get_doc({
                "doctype": "WMS Wave", "warehouse": template.warehouse, "route": template.route,
                "priority": template.priority, "picking_strategy": template.picking_strategy,
                "wave_template": template.name, "status": "Draft",
                "deliveries": [{"outbound_delivery": d} for d in matches[start:start + size]],
            })
            wave.insert(ignore_permissions=True)
            created.append(wave.name)
    return created

def auto_release_due_waves():
    released = []
    for wave in frappe.get_all("WMS Wave", filters={"status": "Draft", "wave_template": ["is", "set"]}, fields=["name", "wave_template"]):
        template = frappe.db.get_value("Wave Template", wave.wave_template, ["auto_release", "cutoff_time", "minimum_deliveries"], as_dict=True)
        if not template or not template.auto_release or not template.cutoff_time: continue
        if template.minimum_deliveries and frappe.db.count("WMS Wave Delivery", {"parent": wave.name}) < template.minimum_deliveries: continue  # too small: left for a supervisor
        cutoff = datetime.combine(getdate(nowdate()), get_time(template.cutoff_time))
        if now_datetime() < cutoff: continue
        released.append(release_wave(wave.name))
    return released


def _locked(wave):
    """From lock_minutes before the template's cutoff a Draft wave is frozen."""
    template = frappe.db.get_value("Wave Template", wave.wave_template, ["cutoff_time", "lock_minutes"], as_dict=True) if wave.wave_template else None
    if not template or not template.cutoff_time or not template.lock_minutes: return False
    cutoff = datetime.combine(getdate(nowdate()), get_time(template.cutoff_time))
    return now_datetime() >= add_to_date(cutoff, minutes=-template.lock_minutes)

def split_wave(wave_name, deliveries):
    """Move some of a Draft wave's deliveries into a new Draft wave with the same settings."""
    require_role("WMS Operator", "WMS Supervisor")
    wave = frappe.get_doc("WMS Wave", wave_name, for_update=True)
    if wave.status != "Draft": frappe.throw(_("Only a Draft wave can be split"))
    if _locked(wave): frappe.throw(_("The wave is locked before its cutoff"))
    move = set(deliveries or [])
    have = {r.outbound_delivery for r in wave.deliveries}
    if not move or not move <= have or move == have: frappe.throw(_("Choose some, but not all, of the deliveries of {0}").format(wave_name))
    new = frappe.get_doc({"doctype": "WMS Wave", "warehouse": wave.warehouse, "route": wave.route, "priority": wave.priority, "picking_strategy": wave.picking_strategy,
        "ship_date": wave.ship_date, "wave_template": wave.wave_template, "status": "Draft", "deliveries": [{"outbound_delivery": d} for d in sorted(move)]})
    new.insert(ignore_permissions=True)
    wave.set("deliveries", [r for r in wave.deliveries if r.outbound_delivery not in move])
    wave.save(ignore_permissions=True)
    return new.name
