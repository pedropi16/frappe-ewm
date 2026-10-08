"""Transportation Units and yard tasks (SAP: transportation unit, yard movements).

A WMS Transportation Unit is the vehicle or container itself: it arrives, is parked on a yard spot (a Storage Bin of a storage type with the Yard
role), is moved by yard tasks to a door (Door role) and back, and departs. It follows its dock appointment: checking the truck in creates its unit,
sending it to a door and checking it out update it. A yard task moves a unit between yard spots and doors; confirming it updates the unit and,
for a door, the appointment.
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

from frappe_wms.utils import require_role, require_storage_role

YARD_ROLES = ("WMS Operator", "WMS Receiver", "WMS Loader", "WMS Supervisor")
LIVE = ("Planned", "In Yard", "At Door", "Loading", "Unloading")
ACTIVITY = {"Planned": "Planned", "In Yard": "Active", "At Door": "Active", "Loading": "Active", "Unloading": "Active", "Departed": "Completed", "Cancelled": "Completed"}  # SAP TU activity
APPOINTMENT_TO_UNIT = {"Planned": "Planned", "Checked In": "In Yard", "At Door": "At Door", "Completed": "At Door", "Checked Out": "Departed", "No Show": "Cancelled", "Cancelled": "Cancelled"}


def _occupant(bin_name, exclude=None):
    return frappe.db.get_value("WMS Transportation Unit", {"status": ["in", LIVE], "name": ["!=", exclude or ""], "yard_bin": bin_name}, "name") \
        or frappe.db.get_value("WMS Transportation Unit", {"status": ["in", LIVE], "name": ["!=", exclude or ""], "door": bin_name}, "name")


def free_yard_spot(warehouse):
    from frappe_wms.services.yard import _role_bins
    return next((b for b in _role_bins(warehouse, "Yard") if not _occupant(b)), None)


def vehicle_for(warehouse, registration, carrier=None, driver_name=None):
    """The vehicle with this registration in the warehouse (SAP: vehicle groups its transportation units); created on first sight."""
    if not registration: return None
    name = frappe.db.get_value("WMS Vehicle", {"warehouse": warehouse, "vehicle_registration": registration}, "name")
    return name or frappe.get_doc({"doctype": "WMS Vehicle", "warehouse": warehouse, "vehicle_registration": registration, "carrier": carrier,
                                   "driver_name": driver_name}).insert(ignore_permissions=True).name


def create_transport_unit(warehouse, unit_type="Truck", carrier=None, vehicle_registration=None, trailer_number=None, driver_name=None, seal_number=None,
                          dock_appointment=None, shipment=None, inbound_delivery=None):
    require_role(*YARD_ROLES)
    doc = frappe.get_doc({"doctype": "WMS Transportation Unit", "warehouse": warehouse, "unit_type": unit_type, "carrier": carrier, "vehicle_registration": vehicle_registration,
        "trailer_number": trailer_number, "driver_name": driver_name, "seal_number": seal_number, "dock_appointment": dock_appointment, "shipment": shipment,
        "inbound_delivery": inbound_delivery, "status": "Planned",
        "vehicle": vehicle_for(warehouse, vehicle_registration, carrier, driver_name)}).insert(ignore_permissions=True)
    return doc.name


def arrive(unit, yard_spot=None, checkpoint=None):
    """Arrival at the checkpoint: the unit takes a yard spot (the one given, else the first free one) and its activity becomes Active."""
    require_role(*YARD_ROLES)
    doc = frappe.get_doc("WMS Transportation Unit", unit, for_update=True)
    if doc.status != "Planned": frappe.throw(_("{0} is {1}").format(unit, _(doc.status)))
    from frappe_wms.services.yard import resolve_checkpoint
    checkpoint = resolve_checkpoint(doc.warehouse, checkpoint)
    spot = yard_spot or free_yard_spot(doc.warehouse)
    if spot:
        require_storage_role(spot, "Yard", label=_("Yard spot"))
        if _occupant(spot, doc.name): frappe.throw(_("Yard spot {0} is occupied by {1}").format(spot, _occupant(spot, doc.name)))
    doc.db_set({"status": "In Yard", "activity_status": "Active", "checkpoint": checkpoint, "yard_bin": spot, "arrived_at": now_datetime()}, update_modified=True)
    return {"unit": doc.name, "status": "In Yard", "yard_bin": spot}


def request_yard_move(unit, to_bin, assigned_resource=None, priority="Normal"):
    require_role(*YARD_ROLES)
    doc = frappe.get_doc("WMS Transportation Unit", unit)
    if doc.status not in ("In Yard", "At Door", "Loading", "Unloading"): frappe.throw(_("{0} is {1}: it cannot be moved in the yard").format(unit, _(doc.status)))
    bin_warehouse = frappe.db.get_value("Storage Bin", to_bin, "warehouse")
    if bin_warehouse != doc.warehouse: frappe.throw(_("{0} is not a bin of warehouse {1}").format(to_bin, doc.warehouse))
    from frappe_wms.utils import storage_bin_role
    if storage_bin_role(to_bin) not in ("Yard", "Door"): frappe.throw(_("A transportation unit moves to a yard spot or a door, not {0}").format(to_bin))
    if _occupant(to_bin, doc.name): frappe.throw(_("{0} is occupied by {1}").format(to_bin, _occupant(to_bin, doc.name)))
    if frappe.db.exists("WMS Yard Task", {"transport_unit": unit, "status": "Open"}): frappe.throw(_("{0} already has an open yard task").format(unit))
    task = frappe.get_doc({"doctype": "WMS Yard Task", "transport_unit": unit, "warehouse": doc.warehouse, "from_bin": doc.door or doc.yard_bin, "to_bin": to_bin,
        "assigned_resource": assigned_resource, "priority": priority, "status": "Open"}).insert(ignore_permissions=True)
    return task.name


def confirm_yard_move(task_name):
    require_role(*YARD_ROLES)
    task = frappe.get_doc("WMS Yard Task", task_name, for_update=True)
    if task.status != "Open": frappe.throw(_("{0} is {1}").format(task_name, _(task.status)))
    unit = frappe.get_doc("WMS Transportation Unit", task.transport_unit, for_update=True)
    from frappe_wms.utils import storage_bin_role
    if _occupant(task.to_bin, unit.name): frappe.throw(_("{0} is occupied by {1}").format(task.to_bin, _occupant(task.to_bin, unit.name)))
    if storage_bin_role(task.to_bin) == "Door":
        appointment = unit.dock_appointment
        if appointment and frappe.db.get_value("WMS Dock Appointment", appointment, "status") in ("Planned", "Checked In"):
            from frappe_wms.services.yard import to_door
            to_door(appointment, task.to_bin)  # books the door on the appointment (and syncs this unit)
        unit.reload()
        unit.db_set({"door": task.to_bin, "yard_bin": None, "status": "At Door"}, update_modified=True)
    else:
        unit.db_set({"yard_bin": task.to_bin, "door": None, "status": "In Yard"}, update_modified=True)
    task.db_set({"status": "Confirmed", "confirmed_at": now_datetime(), "confirmed_by": frappe.session.user}, update_modified=True)
    return {"task": task.name, "unit": unit.name, "status": unit.status}


def start_work(unit, kind):
    """Loading or unloading at the door."""
    require_role(*YARD_ROLES)
    if kind not in ("Loading", "Unloading"): frappe.throw(_("Choose Loading or Unloading"))
    doc = frappe.get_doc("WMS Transportation Unit", unit, for_update=True)
    if doc.status != "At Door": frappe.throw(_("{0} must be at a door first").format(unit))
    limit = doc.means_of_transport and frappe.db.get_value("Means of Transport", doc.means_of_transport, "max_payload_kg")
    weight = kind == "Loading" and doc.shipment and frappe.db.get_value("WMS Shipment", doc.shipment, "total_weight")
    if limit and weight and weight > limit:
        frappe.throw(_("Shipment {0} weighs {1} kg: {2} carries at most {3} kg").format(doc.shipment, weight, doc.means_of_transport, limit))
    doc.db_set("status", kind, update_modified=True)
    return {"unit": unit, "status": kind}


def depart(unit, checkpoint=None):
    require_role(*YARD_ROLES)
    doc = frappe.get_doc("WMS Transportation Unit", unit, for_update=True)
    if doc.status not in ("In Yard", "At Door", "Loading", "Unloading"): frappe.throw(_("{0} is {1}").format(unit, _(doc.status)))
    from frappe_wms.services.yard import require_seal, resolve_checkpoint
    checkpoint = resolve_checkpoint(doc.warehouse, checkpoint)
    appointment = doc.dock_appointment and frappe.db.get_value("WMS Dock Appointment", doc.dock_appointment, ["status", "direction"], as_dict=True)
    direction = appointment.direction if appointment else ("Outbound" if doc.shipment else None)
    require_seal(doc.warehouse, direction, doc.seal_number)
    if frappe.db.exists("WMS Yard Task", {"transport_unit": unit, "status": "Open"}): frappe.throw(_("{0} still has an open yard task").format(unit))
    doc.db_set({"status": "Departed", "activity_status": "Completed", "departed_at": now_datetime(), "yard_bin": None, "door": None}, update_modified=True)
    if doc.dock_appointment and frappe.db.get_value("WMS Dock Appointment", doc.dock_appointment, "status") in ("Checked In", "At Door", "Completed"):
        from frappe_wms.services.yard import check_out
        check_out(doc.dock_appointment, checkpoint)
    return {"unit": unit, "status": "Departed"}


YARD_STATUS = {"Planned": "Expected", "In Yard": "In Yard", "At Door": "At Door", "Loading": "At Door", "Unloading": "At Door", "Departed": "Departed"}


def carried(appointment_name):
    """(inbound deliveries, shipments) a truck brings: the appointment's own plus those of each of its units."""
    a = frappe.db.get_value("WMS Dock Appointment", appointment_name, ["inbound_delivery", "shipment"], as_dict=True) or frappe._dict()
    units = frappe.get_all("WMS Transportation Unit", filters={"dock_appointment": appointment_name}, fields=["inbound_delivery", "shipment"])
    return ({x for x in [a.inbound_delivery] + [u.inbound_delivery for u in units] if x}, {x for x in [a.shipment] + [u.shipment for u in units] if x})


def _sync_documents(appointment, unit_status):
    """Tell the deliveries / shipments the truck carries where that truck is (SAP: TU assignment status on the delivery)."""
    value = YARD_STATUS.get(unit_status)
    if not value: return
    inbound, shipments = carried(appointment.name)
    for name in inbound: frappe.db.set_value("Inbound Delivery", name, "yard_status", value, update_modified=False)
    for name in shipments: frappe.db.set_value("WMS Shipment", name, "yard_status", value, update_modified=False)


def sync_from_appointment(appointment):
    """Follow the dock appointment: the gate check-in creates the unit, the door and the check-out update it."""
    status = APPOINTMENT_TO_UNIT.get(appointment.status)
    if not status or appointment.status == "Planned": return None
    units = frappe.get_all("WMS Transportation Unit", filters={"dock_appointment": appointment.name}, pluck="name")
    unit = units[0] if units else None
    values = {"status": status, "activity_status": ACTIVITY[status], "yard_bin": appointment.yard_bin if status == "In Yard" else None, "door": appointment.door if status == "At Door" else None}
    if status == "Departed": values["departed_at"] = now_datetime()
    _sync_documents(appointment, status)
    if unit:
        for name in units: frappe.db.set_value("WMS Transportation Unit", name, values, update_modified=True)  # a vehicle's units move together
        return unit
    doc = frappe.get_doc({"doctype": "WMS Transportation Unit", "warehouse": appointment.warehouse, "unit_type": "Truck", "carrier": appointment.carrier, "vehicle_registration": appointment.vehicle_registration,
        "trailer_number": appointment.trailer_number, "driver_name": appointment.driver_name, "dock_appointment": appointment.name, "shipment": appointment.shipment,
        "inbound_delivery": appointment.inbound_delivery, "checkpoint": appointment.checkpoint, "vehicle": vehicle_for(appointment.warehouse, appointment.vehicle_registration, appointment.carrier, appointment.driver_name), "arrived_at": appointment.checked_in_at or now_datetime(), **values}).insert(ignore_permissions=True)
    return doc.name
