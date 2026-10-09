import frappe
from frappe import _
from frappe_wms.services import locks
from frappe_wms.utils import parse_json, require_role


@frappe.whitelist()
def acquire_lock(object_type, object_name, purpose=None):
    """Opens the object for change: everybody else can display it, nobody can change it until the lock is released."""
    return locks.acquire(object_type, object_name, purpose)


@frappe.whitelist()
def acquire_locks(objects, purpose=None):
    locks.acquire_many([tuple(o) for o in parse_json(objects, "objects")], purpose)


@frappe.whitelist()
def release_lock(object_type, object_name):
    locks.release(object_type, object_name)


@frappe.whitelist()
def release_locks(objects):
    for o in parse_json(objects, "objects"): locks.release(*o)


@frappe.whitelist()
def renew_locks(objects):
    return [locks.renew(*o) for o in parse_json(objects, "objects")]


@frappe.whitelist()
def lock_status(object_type, object_name):
    return locks.status(object_type, object_name)


@frappe.whitelist()
def list_locks():
    """Lock entries (SAP SM12)."""
    require_role("WMS Administrator", "WMS Supervisor", "System Manager")
    return locks.all_locks()


@frappe.whitelist()
def delete_lock(object_type, object_name):
    require_role("WMS Administrator", "WMS Supervisor", "System Manager")
    locks.release(object_type, object_name, force=True)
