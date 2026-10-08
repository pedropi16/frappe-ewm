import frappe
from frappe_wms.services import yard
from frappe_wms.services.concurrency import retry_on_deadlock


@frappe.whitelist()
def yard_board(warehouse, date=None):
    from frappe_wms.utils import require_wms_access
    require_wms_access()
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    return yard.yard_board(warehouse, date)


@frappe.whitelist()
def free_slots(warehouse, date, minutes=None):
    from frappe_wms.utils import require_wms_access
    require_wms_access()
    return yard.free_slots(warehouse, date, minutes)


@frappe.whitelist()
@retry_on_deadlock
def create_appointment(warehouse, direction, planned_start, planned_end=None, door=None, carrier=None, vehicle_registration=None,
                       trailer_number=None, driver_name=None, inbound_delivery=None, shipment=None, remarks=None):
    return yard.create_appointment(warehouse, direction, planned_start, planned_end, door, carrier, vehicle_registration,
                                   trailer_number, driver_name, inbound_delivery, shipment, remarks)


@frappe.whitelist()
@retry_on_deadlock
def check_in(warehouse, appointment=None, vehicle_registration=None, direction=None, carrier=None, trailer_number=None,
             driver_name=None, yard_bin=None, confirm_without_appointment=0, checkpoint=None):
    return yard.check_in(warehouse, appointment, vehicle_registration, direction, carrier, trailer_number, driver_name, yard_bin,
                         confirm_without_appointment, checkpoint)


@frappe.whitelist()
@retry_on_deadlock
def to_door(appointment, door=None):
    return yard.to_door(appointment, door)


@frappe.whitelist()
@retry_on_deadlock
def complete(appointment):
    return yard.complete(appointment)


@frappe.whitelist()
@retry_on_deadlock
def check_out(appointment, checkpoint=None):
    return yard.check_out(appointment, checkpoint)


@frappe.whitelist()
@retry_on_deadlock
def cancel(appointment, reason=None):
    return yard.cancel(appointment, reason)


@frappe.whitelist()
@retry_on_deadlock
def create_transport_unit(warehouse, unit_type="Truck", carrier=None, vehicle_registration=None, trailer_number=None, driver_name=None, seal_number=None,
                          dock_appointment=None, shipment=None, inbound_delivery=None):
    from frappe_wms.services import transport_unit as tu
    return tu.create_transport_unit(warehouse, unit_type, carrier, vehicle_registration, trailer_number, driver_name, seal_number, dock_appointment, shipment, inbound_delivery)


@frappe.whitelist()
@retry_on_deadlock
def arrive_transport_unit(unit, yard_spot=None, checkpoint=None):
    from frappe_wms.services import transport_unit as tu
    return tu.arrive(unit, yard_spot, checkpoint)


@frappe.whitelist()
@retry_on_deadlock
def request_yard_move(unit, to_bin, assigned_resource=None, priority="Normal"):
    from frappe_wms.services import transport_unit as tu
    return tu.request_yard_move(unit, to_bin, assigned_resource, priority)


@frappe.whitelist()
@retry_on_deadlock
def confirm_yard_move(task):
    from frappe_wms.services import transport_unit as tu
    return tu.confirm_yard_move(task)


@frappe.whitelist()
@retry_on_deadlock
def start_transport_unit_work(unit, kind):
    from frappe_wms.services import transport_unit as tu
    return tu.start_work(unit, kind)


@frappe.whitelist()
@retry_on_deadlock
def depart_transport_unit(unit, checkpoint=None):
    from frappe_wms.services import transport_unit as tu
    return tu.depart(unit, checkpoint)


@frappe.whitelist()
def cockpit(warehouse):
    from frappe_wms.utils import require_wms_access
    require_wms_access()
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    return yard.cockpit(warehouse)


@frappe.whitelist()
@retry_on_deadlock
def plan_truck(warehouse, direction, planned_start, inbound_delivery=None, outbound_deliveries=None, shipment=None, carrier=None, vehicle_registration=None,
               trailer_number=None, driver_name=None, planned_end=None, door=None, means_of_transport=None, route=None, inbound_deliveries=None, shipments=None):
    return yard.plan_truck(warehouse, direction, planned_start, inbound_delivery, frappe.parse_json(outbound_deliveries) if outbound_deliveries else None, shipment,
                           carrier, vehicle_registration, trailer_number, driver_name, planned_end, door, means_of_transport, route,
                           frappe.parse_json(inbound_deliveries) if inbound_deliveries else None, frappe.parse_json(shipments) if shipments else None)


@frappe.whitelist()
@retry_on_deadlock
def create_recurring_appointments(warehouse, direction, first_start, repeat="Weekly", count=4, carrier=None, vehicle_registration=None, door=None, planned_end=None):
    return yard.create_recurring_appointments(warehouse, direction, first_start, repeat, count, carrier=carrier, vehicle_registration=vehicle_registration, door=door, planned_end=planned_end)
