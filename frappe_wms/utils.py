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
