import frappe
from frappe_wms.services.movement import close_movement as _close_movement


@frappe.whitelist()
def close_movement(hu_name, device=None):
    return _close_movement(hu_name, device)
