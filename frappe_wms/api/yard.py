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
             driver_name=None, yard_bin=None, confirm_without_appointment=0):
    return yard.check_in(warehouse, appointment, vehicle_registration, direction, carrier, trailer_number, driver_name, yard_bin,
                         confirm_without_appointment)


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
def check_out(appointment):
    return yard.check_out(appointment)


@frappe.whitelist()
@retry_on_deadlock
def cancel(appointment, reason=None):
    return yard.cancel(appointment, reason)
