import json
import frappe
from frappe import _
from frappe.utils import flt

def ensure_same_warehouse(*docs_or_names):
    warehouses = {getattr(x, "warehouse", None) for x in docs_or_names if getattr(x, "warehouse", None)}
    if len(warehouses) > 1:
        frappe.throw(_("Warehouse mismatch between transaction objects"))

def parse_json(value, label="payload"):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except Exception:
        frappe.throw(_("Invalid JSON in {0}").format(label))

def positive(value, label):
    value = flt(value)
    if value <= 0:
        frappe.throw(_("{0} must be greater than zero").format(label))
    return value

def require_role(*roles):
    if "System Manager" in frappe.get_roles():
        return
    if not set(roles).intersection(frappe.get_roles()):
        frappe.throw(_("You are not permitted to perform this warehouse operation"), frappe.PermissionError)

def storage_bin_role(bin_name):
    # A bin's role comes from its Storage Type (storage_role) - the same field allocation.py
    # already reads to keep Receiving/Staging/Door/etc. stock out of the allocatable pool.
    if not bin_name: return None
    storage_type = frappe.db.get_value("Storage Bin", bin_name, "storage_type")
    return frappe.db.get_value("Storage Type", storage_type, "storage_role") if storage_type else None

def require_storage_role(bin_name, role, label=None):
    if storage_bin_role(bin_name) != role:
        frappe.throw(_("{0} {1} is not configured as a {2} bin").format(label or _("Storage Bin"), bin_name, role))
