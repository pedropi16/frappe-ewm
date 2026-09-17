import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.utils import require_role

# Mirrors SAP EWM RF logon: a Resource is a stable, admin-configured physical device (already
# scoped to its activity area via WMS Resource Group / Warehouse Queue, see README's "Resources,
# Resource Groups & queueing"); a user logs on to one at the start of a shift and logs off (or is
# kicked by a Supervisor) at the end, rather than an admin permanently wiring one user to one
# device in the desk form.
RESOURCE_ROLES = ("WMS Operator", "WMS Receiver", "WMS Picker", "WMS Packer", "WMS Loader", "WMS Inventory Controller", "WMS Supervisor")

def list_available_resources(warehouse=None):
    require_role(*RESOURCE_ROLES)
    filters = {"active": 1, "user": ["in", ["", None]]}
    if warehouse: filters["warehouse"] = warehouse
    return frappe.get_list("WMS Resource", filters=filters,
        fields=["name", "resource_code", "warehouse", "resource_type", "resource_group", "device_id"],
        order_by="resource_code asc", limit=200)

def log_on(resource_code, user=None):
    require_role(*RESOURCE_ROLES)
    user = user or frappe.session.user
    frappe.db.sql("select name from `tabWMS Resource` where name=%s for update", resource_code)
    resource = frappe.get_doc("WMS Resource", resource_code)
    if not resource.active: frappe.throw(_("WMS Resource {0} is not active").format(resource_code))
    if resource.user and resource.user != user:
        frappe.throw(_("WMS Resource {0} is already logged on by {1}").format(resource_code, resource.user))
    # A user is only ever logged on to one Resource at a time - logging on to a new one
    # transparently logs them off whatever they were on before, so a forgotten logoff on a
    # different device can never lock them out of this one.
    other_resources = frappe.get_all("WMS Resource", filters={"user": user, "name": ["!=", resource_code]}, pluck="name")
    for other in other_resources:
        frappe.db.set_value("WMS Resource", other, {"user": None, "logged_in_at": None})
    resource.db_set({"user": user, "logged_in_at": now_datetime()}, update_modified=True)
    return {"resource": resource.name, "warehouse": resource.warehouse, "current_queue": resource.current_queue}

def log_off(resource_code=None, user=None):
    require_role(*RESOURCE_ROLES)
    user = user or frappe.session.user
    filters = {"user": user}
    if resource_code: filters["name"] = resource_code
    resource = frappe.db.get_value("WMS Resource", filters, "name")
    if not resource: return {"resource": None}
    frappe.db.set_value("WMS Resource", resource, {"user": None, "logged_in_at": None})
    return {"resource": resource}

def kick(resource_code, reason=None):
    require_role("WMS Supervisor")
    resource = frappe.get_doc("WMS Resource", resource_code)
    if not resource.user: frappe.throw(_("WMS Resource {0} has no user logged on").format(resource_code))
    kicked_user = resource.user
    resource.db_set({"user": None, "logged_in_at": None}, update_modified=True)
    return {"resource": resource.name, "kicked_user": kicked_user}
