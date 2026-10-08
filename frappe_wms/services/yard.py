"""Yard and dock appointments (SAP EWM dock appointment scheduling + yard management basics).

A WMS Dock Appointment books a door for a time slot and follows the truck:

  Planned -> Checked In (at the gate, parked on a yard spot) -> At Door -> Completed -> Checked Out
           \\-> No Show (not checked in in time; frees the door)   \\-> Cancelled

Doors are Storage Bins whose storage type has the Door role, yard spots those with the Yard role.
Two active appointments never overlap at one door (plus the warehouse's changeover time). An
outbound truck at a door becomes its shipment's loading door, and departing the shipment
completes the appointment. Settings: WMS Warehouse > Yard and Dock Appointments.
"""
import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, getdate, now_datetime, time_diff_in_seconds

from frappe_wms.utils import require_role

YARD_ROLES = ("WMS Operator", "WMS Receiver", "WMS Loader", "WMS Supervisor")
ACTIVE = ("Planned", "Checked In", "At Door")


def _role_bins(warehouse, role):
    types = frappe.get_all("Storage Type", filters={"warehouse": warehouse, "storage_role": role}, pluck="name")
    if not types: return []
    return frappe.get_all("Storage Bin", filters={"warehouse": warehouse, "storage_type": ["in", types], "active": 1},
                          pluck="name", order_by="sequence asc, name asc")


def doors(warehouse):
    return _role_bins(warehouse, "Door")


def _settings(warehouse):
    return frappe.db.get_value("WMS Warehouse", warehouse, ["dock_slot_minutes", "dock_changeover_minutes", "appointment_check",
                                                            "no_show_after_minutes", "yard_checkpoint_required", "yard_gate_check", "seal_required"], as_dict=True) or frappe._dict()


def resolve_checkpoint(warehouse, checkpoint):
    """The gate bin of an arrival or departure (SAP: arrival / departure at checkpoint). Required when the warehouse says so."""
    if checkpoint:
        from frappe_wms.utils import require_storage_role
        require_storage_role(checkpoint, "Checkpoint", label=_("Checkpoint"))
        if frappe.db.get_value("Storage Bin", checkpoint, "warehouse") != warehouse: frappe.throw(_("{0} is not a bin of warehouse {1}").format(checkpoint, warehouse))
    elif cint(_settings(warehouse).yard_checkpoint_required): frappe.throw(_("Name the checkpoint the truck passes"))
    return checkpoint


def require_seal(warehouse, direction, seal_number):
    if cint(_settings(warehouse).seal_required) and direction == "Outbound" and not seal_number:
        frappe.throw(_("An outbound truck needs a seal number before it leaves"))


def gate_check(warehouse, inbound_delivery=None, shipment=None):
    """Receiving or loading a delivery whose truck has a dock appointment needs that truck at a door (Warehouse > Receipt / Loading Needs Truck at Door)."""
    mode = _settings(warehouse).yard_gate_check or "Off"
    if mode == "Off": return
    filters = {"status": ["in", ["Planned", "Checked In"]]}
    if inbound_delivery: filters["inbound_delivery"] = inbound_delivery
    elif shipment: filters["shipment"] = shipment
    else: return
    waiting = frappe.db.get_value("WMS Dock Appointment", filters, "name")
    if not waiting: return
    message = _("Truck {0} is not at a door yet").format(waiting)
    if mode == "Block": frappe.throw(message)
    frappe.msgprint(message, indicator="orange", alert=True)


def _clashes(door, start, end, warehouse, exclude=None):
    """Active appointments at this door whose slot (plus changeover) overlaps [start, end)."""
    gap = cint(_settings(warehouse).dock_changeover_minutes)
    rows = frappe.get_all("WMS Dock Appointment", filters={"door": door, "status": ["in", ACTIVE], "name": ["!=", exclude or ""]},
                          fields=["name", "planned_start", "planned_end", "vehicle_registration", "status"])
    return [r for r in rows if get_datetime(r.planned_start) < add_to_date(end, minutes=gap)
            and add_to_date(get_datetime(r.planned_end), minutes=gap) > start]


def _occupant(door, exclude=None):
    """The truck physically at this door right now, if any."""
    return frappe.db.get_value("WMS Dock Appointment", {"door": door, "status": "At Door", "name": ["!=", exclude or ""]},
                               ["name", "vehicle_registration"], as_dict=True)


def determined_doors(warehouse, direction=None, route=None):
    """Doors the Door Determination Rules name for this truck (SAP: door determination), best rule first."""
    rules = frappe.get_all("Door Determination Rule", filters={"warehouse": warehouse, "active": 1}, fields=["door", "direction", "route"], order_by="priority asc, name asc")
    out = []
    for r in rules:
        if r.direction not in (None, "", "Both") and r.direction != direction: continue
        if r.route and r.route != route: continue
        if r.door not in out: out.append(r.door)
    return out


def free_door(warehouse, start, end, exclude=None, now=False, direction=None, route=None):
    """The first free door: the ones the determination rules name come first, then the warehouse's other doors.
    Free = no overlapping booking; with now=True also empty right now (a truck still at a door holds it
    even when its slot is over or has not started)."""
    preferred = determined_doors(warehouse, direction, route)
    ordered = preferred + [d for d in doors(warehouse) if d not in preferred]
    return next((d for d in ordered if not _clashes(d, start, end, warehouse, exclude) and not (now and _occupant(d, exclude))), None)


def validate_appointment(doc):
    from frappe_wms.utils import require_storage_role
    settings = _settings(doc.warehouse)
    if not doc.planned_end:
        doc.planned_end = add_to_date(get_datetime(doc.planned_start), minutes=cint(settings.dock_slot_minutes) or 60)
    start, end = get_datetime(doc.planned_start), get_datetime(doc.planned_end)
    if end <= start: frappe.throw(_("The appointment must end after it starts"))
    if doc.direction == "Inbound" and doc.shipment: frappe.throw(_("An inbound appointment cannot carry a shipment"))
    if doc.direction == "Outbound" and doc.inbound_delivery: frappe.throw(_("An outbound appointment cannot carry an inbound delivery"))
    if doc.status not in ACTIVE: return
    # A walk-in waits in the yard without a door until it is sent to one.
    if not doc.door and doc.walk_in and doc.status == "Checked In": return
    if not doc.door:
        route = frappe.db.get_value("WMS Shipment", doc.shipment, "route") if doc.shipment else None
        doc.door = (frappe.db.get_value("WMS Shipment", doc.shipment, "door") if doc.shipment else None) or free_door(doc.warehouse, start, end, doc.name, direction=doc.direction, route=route)
        if not doc.door: frappe.throw(_("No door is free in warehouse {0} from {1} to {2}").format(doc.warehouse, start, end))
    require_storage_role(doc.door, "Door", label=_("Door"))
    # A truck already at its door holds it, whatever else was planned there.
    clash = [] if doc.status == "At Door" else _clashes(doc.door, start, end, doc.warehouse, doc.name)
    if clash:
        c = clash[0]
        frappe.throw(_("Door {0} is booked from {1} to {2} ({3} {4})").format(doc.door, c.planned_start, c.planned_end, c.name, c.vehicle_registration or ""))


# ------------------------------------------------------------------ scheduling

def create_appointment(warehouse, direction, planned_start, planned_end=None, door=None, carrier=None, vehicle_registration=None,
                       trailer_number=None, driver_name=None, inbound_delivery=None, shipment=None, remarks=None):
    require_role("WMS Supervisor", "WMS Receiver", "WMS Loader", "WMS Integration User")
    doc = frappe.get_doc({"doctype": "WMS Dock Appointment", "warehouse": warehouse, "direction": direction, "planned_start": planned_start,
                          "planned_end": planned_end, "door": door, "carrier": carrier, "vehicle_registration": vehicle_registration,
                          "trailer_number": trailer_number, "driver_name": driver_name, "inbound_delivery": inbound_delivery,
                          "shipment": shipment, "remarks": remarks, "status": "Planned"})
    doc.insert(ignore_permissions=True)
    return doc.name


def free_slots(warehouse, date, minutes=None, from_hour=6, to_hour=22):
    """For the booking dialog: per door, the start times on `date` a slot of `minutes` still fits."""
    minutes = cint(minutes) or cint(_settings(warehouse).dock_slot_minutes) or 60
    day = get_datetime(getdate(date))
    out = []
    for door in doors(warehouse):
        starts, t = [], add_to_date(day, hours=cint(from_hour))
        last = add_to_date(day, hours=cint(to_hour))
        while add_to_date(t, minutes=minutes) <= last:
            if not _clashes(door, t, add_to_date(t, minutes=minutes), warehouse): starts.append(str(t))
            t = add_to_date(t, minutes=30)
        out.append({"door": door, "free_starts": starts})
    return out


def yard_board(warehouse, date=None):
    """What the Monitor's Yard & Doors view and the RF Yard screen show."""
    day = getdate(date) if date else getdate()
    rows = frappe.get_all("WMS Dock Appointment", filters={"warehouse": warehouse,
                          "planned_start": ["between", [str(day), f"{day} 23:59:59"]]}, fields=["*"], order_by="planned_start asc")
    # trucks from earlier days still expected (late, not yet a no-show), in the yard or at a door
    # belong on today's board too
    names = {r.name for r in rows}
    rows += [r for r in frappe.get_all("WMS Dock Appointment", filters={"warehouse": warehouse, "status": ["in", ["Planned", "Checked In", "At Door"]],
                                                                        "planned_start": ["<", str(day)]},
                                       fields=["*"], order_by="planned_start asc") if r.name not in names]
    at_door = {r.door: r.name for r in rows if r.status == "At Door"}
    return {"date": str(day), "appointments": rows, "doors": [{"door": d, "occupied_by": at_door.get(d)} for d in doors(warehouse)], "checkpoints": _role_bins(warehouse, "Checkpoint"),
            "yard_spots": _role_bins(warehouse, "Yard"), "settings": _settings(warehouse)}


# ------------------------------------------------------------------ the truck's way through the yard

def _get(name, *statuses):
    doc = frappe.get_doc("WMS Dock Appointment", name, for_update=True)
    if statuses and doc.status not in statuses:
        frappe.throw(_("{0} is {1}").format(name, _(doc.status)))
    return doc


def check_in(warehouse, appointment=None, vehicle_registration=None, direction=None, carrier=None, trailer_number=None,
             driver_name=None, yard_bin=None, confirm_without_appointment=0, checkpoint=None):
    """At the gate. Without an appointment, the warehouse's rule decides (Allow / Warn / Block)."""
    require_role(*YARD_ROLES)
    now = now_datetime()
    if not appointment and vehicle_registration:
        appointment = frappe.db.get_value("WMS Dock Appointment", {"warehouse": warehouse, "vehicle_registration": vehicle_registration,
                                          "status": "Planned"}, "name", order_by="planned_start asc")
    if appointment:
        doc = _get(appointment, "Planned")
        doc.arrival_delay_minutes = int(time_diff_in_seconds(now, doc.planned_start) // 60)
    else:
        rule = _settings(warehouse).appointment_check or "Allow"
        if rule == "Block": frappe.throw(_("No appointment for {0} - this warehouse only takes trucks with an appointment").format(vehicle_registration or _("this truck")))
        if rule == "Warn" and not cint(confirm_without_appointment):
            return {"needs_confirmation": _("{0} has no appointment. Check it in anyway?").format(vehicle_registration or _("This truck"))}
        if direction not in ("Inbound", "Outbound"): frappe.throw(_("Say whether the truck delivers (Inbound) or collects (Outbound)"))
        doc = frappe.get_doc({"doctype": "WMS Dock Appointment", "warehouse": warehouse, "direction": direction, "walk_in": 1,
                              "planned_start": now, "planned_end": add_to_date(now, minutes=cint(_settings(warehouse).dock_slot_minutes) or 60),
                              "vehicle_registration": vehicle_registration})
    checkpoint = resolve_checkpoint(warehouse, checkpoint)
    for field, value in (("carrier", carrier), ("trailer_number", trailer_number), ("driver_name", driver_name), ("vehicle_registration", vehicle_registration)):
        if value: doc.set(field, value)
    if yard_bin:
        from frappe_wms.utils import require_storage_role
        require_storage_role(yard_bin, "Yard", label=_("Yard spot"))
    doc.update({"status": "Checked In", "checked_in_at": now, "yard_bin": yard_bin, "checkpoint": checkpoint})
    if doc.is_new(): doc.insert(ignore_permissions=True)
    else: doc.save(ignore_permissions=True)
    from frappe_wms.services.transport_unit import sync_from_appointment
    sync_from_appointment(doc)  # the truck's transportation unit
    return {"appointment": doc.name, "status": doc.status, "door": doc.door, "yard_bin": doc.yard_bin}


def to_door(appointment, door=None):
    """The truck drives from the yard to its door (or the one given)."""
    require_role(*YARD_ROLES)
    doc = _get(appointment, "Planned", "Checked In")
    door = door or doc.door
    if not door:
        now = now_datetime()
        route = frappe.db.get_value("WMS Shipment", doc.shipment, "route") if doc.shipment else None
        door = free_door(doc.warehouse, now, max(get_datetime(doc.planned_end), add_to_date(now, minutes=30)), doc.name, now=True, direction=doc.direction, route=route)
        if not door: frappe.throw(_("No door is free right now"))
    occupant = _occupant(door, doc.name)
    if occupant: frappe.throw(_("Door {0} is still occupied by {1} ({2})").format(door, occupant.vehicle_registration or "", occupant.name))
    doc.door = door
    doc.update({"status": "At Door", "docked_at": now_datetime(), "checked_in_at": doc.checked_in_at or now_datetime()})
    doc.save(ignore_permissions=True)
    if doc.shipment and frappe.db.get_value("WMS Shipment", doc.shipment, "status") in ("Ready to Load", "Loading", "Loaded"):
        frappe.db.set_value("WMS Shipment", doc.shipment, "door", door)
    from frappe_wms.services.transport_unit import sync_from_appointment
    sync_from_appointment(doc)
    return {"appointment": doc.name, "status": doc.status, "door": door}


def complete(appointment):
    require_role(*YARD_ROLES)
    doc = _get(appointment, "At Door")
    doc.db_set({"status": "Completed", "completed_at": now_datetime()}, update_modified=True)
    return {"appointment": doc.name, "status": "Completed"}


def check_out(appointment, checkpoint=None):
    require_role(*YARD_ROLES)
    doc = _get(appointment, "Checked In", "At Door", "Completed")
    checkpoint = resolve_checkpoint(doc.warehouse, checkpoint)
    seal = frappe.db.get_value("WMS Transportation Unit", {"dock_appointment": doc.name}, "seal_number")
    require_seal(doc.warehouse, doc.direction, seal)
    now = now_datetime()
    doc.db_set({"status": "Checked Out", "checked_out_at": now, "departure_checkpoint": checkpoint, "completed_at": doc.completed_at or (now if doc.status == "At Door" else None)},
               update_modified=True)
    doc.status = "Checked Out"
    from frappe_wms.services.transport_unit import sync_from_appointment
    sync_from_appointment(doc)
    return {"appointment": doc.name, "status": "Checked Out"}


def cancel(appointment, reason=None):
    require_role("WMS Supervisor", "WMS Receiver", "WMS Loader", "WMS Integration User")
    doc = _get(appointment, "Planned")
    doc.db_set({"status": "Cancelled", "remarks": "\n".join(x for x in (doc.remarks, reason) if x)}, update_modified=True)
    return {"appointment": doc.name, "status": "Cancelled"}


def on_shipment_departed(shipment_name):
    """Departing a shipment completes its truck's appointment."""
    for name in frappe.get_all("WMS Dock Appointment", filters={"shipment": shipment_name, "status": ["in", ["Checked In", "At Door"]]}, pluck="name"):
        frappe.db.set_value("WMS Dock Appointment", name, {"status": "Completed", "completed_at": now_datetime()}, update_modified=True)


def mark_no_shows():
    """Scheduler (hourly): planned trucks that never came free their door."""
    for w in frappe.get_all("WMS Warehouse", filters={"active": 1}, fields=["name", "no_show_after_minutes"]):
        minutes = cint(w.no_show_after_minutes)
        if not minutes: continue
        late = add_to_date(now_datetime(), minutes=-minutes)
        for name in frappe.get_all("WMS Dock Appointment", filters={"warehouse": w.name, "status": "Planned", "planned_start": ["<", late]}, pluck="name"):
            frappe.db.set_value("WMS Dock Appointment", name, "status", "No Show", update_modified=True)
