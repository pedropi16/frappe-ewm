import frappe
from frappe import _
from frappe_wms.services import locks
from frappe_wms.utils import parse_json, require_role, require_wms_access


@frappe.whitelist()
def acquire_lock(object_type, object_name, purpose=None):
    """Opens the object for change: everybody else can display it, nobody can change it until the lock is released."""
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    return locks.acquire(object_type, object_name, purpose)


@frappe.whitelist()
def acquire_locks(objects, purpose=None):
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    locks.acquire_many([tuple(o) for o in parse_json(objects, "objects")], purpose)


@frappe.whitelist()
def acquire_available_locks(objects, purpose=None):
    """For screens that open in change mode but can show locked objects for display: {name: user} of the ones somebody else holds."""
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    return locks.acquire_available([tuple(o) for o in parse_json(objects, "objects")], purpose)


@frappe.whitelist()
def release_lock(object_type, object_name):
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    locks.release(object_type, object_name)


@frappe.whitelist()
def release_locks(objects):
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    for o in parse_json(objects, "objects"): locks.release(*o)


@frappe.whitelist()
def renew_locks(objects):
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    return [locks.renew(*o) for o in parse_json(objects, "objects")]


@frappe.whitelist()
def lock_status(object_type, object_name):
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    return locks.status(object_type, object_name)


@frappe.whitelist()
def list_locks():
    """Lock entries (SAP SM12)."""
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    require_role("WMS Administrator", "WMS Supervisor", "System Manager")
    return locks.all_locks()


@frappe.whitelist()
def delete_lock(object_type, object_name):
    require_wms_access()  # any logged-in user could otherwise lock any object for the operators
    require_role("WMS Administrator", "WMS Supervisor", "System Manager")
    locks.release(object_type, object_name, force=True)
