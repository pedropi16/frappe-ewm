import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.task import my_resource
from frappe_wms.services.handling_unit import full_hu_quantity
from frappe_wms.utils import require_role

OPEN_VAS_STATUSES = ("Open", "In Process")


def list_open_vas_orders(user=None):
    require_role("WMS Operator", "WMS Packer", "WMS Supervisor")
    resource = my_resource(user)
    filters = {"status": ["in", OPEN_VAS_STATUSES]}
    if resource: filters["warehouse"] = resource.warehouse
    orders = frappe.get_list("VAS Order", filters=filters,
        fields=["name", "handling_unit", "warehouse", "work_center_bin", "status", "priority"],
        order_by="priority desc, creation asc", limit=50)
    for order in orders:
        rows = frappe.get_all("VAS Order Activity", filters={"parent": order.name}, fields=["completed"])
        order["activity_count"] = len(rows)
        order["completed_count"] = sum(1 for r in rows if r.completed)
    return orders


def get_vas_order(vas_order_name):
    require_role("WMS Operator", "WMS Packer", "WMS Supervisor")
    doc = frappe.get_doc("VAS Order", vas_order_name)
    doc.check_permission("read")
    return doc.as_dict()


def complete_vas_activity(vas_order_name, activity_row_name, remarks=None):
    require_role("WMS Operator", "WMS Packer", "WMS Supervisor")
    frappe.db.sql("select name from `tabVAS Order` where name=%s for update", vas_order_name)
    order = frappe.get_doc("VAS Order", vas_order_name)
    if order.status not in OPEN_VAS_STATUSES: frappe.throw(_("VAS Order is not open"))
    row = next((r for r in order.activities if r.name == activity_row_name), None)
    if not row: frappe.throw(_("Activity {0} not found on this VAS Order").format(activity_row_name))
    if row.completed: return {"vas_order": order.name, "status": order.status, "already_completed": True}
    completed_at = now_datetime()
    duration_seconds = (completed_at - row.started_at).total_seconds() if row.started_at else None
    row.db_set({"completed": 1, "completed_by": frappe.session.user, "completed_at": completed_at,
        "duration_seconds": duration_seconds, "remarks": remarks}, update_modified=True)

    rows = frappe.get_all("VAS Order Activity", filters={"parent": order.name}, fields=["completed"])
    all_done = bool(rows) and all(r.completed for r in rows)
    updates = {"status": "Completed" if all_done else "In Process"}
    if all_done: updates.update({"completed_at": now_datetime(), "completed_by": frappe.session.user})
    order.db_set(updates, update_modified=True)
    return {"vas_order": order.name, "status": updates["status"]}


def create_vas_order_from_packaging_spec(handling_unit, work_center_bin):
    # Ties VAS depth to Packaging Spec (P2): one "Kit" activity per configured level, with
    # the cumulative quantity that level represents, reusing full_hu_quantity's own cumulative
    # multiplier chain rather than recomputing it. An explicit, user-triggered action - an
    # item with no Packaging Spec is a real error to surface, not something to skip quietly.
    require_role("WMS Operator", "WMS Packer", "WMS Supervisor")
    hu = frappe.get_doc("Handling Unit", handling_unit)
    item = frappe.db.get_value("WMS Stock Balance", {"handling_unit": handling_unit, "quantity": [">", 0]}, "product")
    if not item: frappe.throw(_("Handling Unit {0} has no stock to build VAS activities for").format(handling_unit))
    if not frappe.db.exists("Packaging Spec", {"item": item, "active": 1}):
        frappe.throw(_("Item {0} has no active Packaging Spec").format(item))
    stock_uom = frappe.db.get_value("WMS Product", {"item": item}, "stock_uom")
    levels = frappe.get_all("Packaging Spec Level", filters={"parent": item, "parenttype": "Packaging Spec"}, fields=["level_name"], order_by="idx asc")
    activities = [{
        "step_no": i, "activity_type": "Kit", "instruction": _("Kit to {0}").format(level.level_name),
        "quantity": full_hu_quantity(item, level.level_name), "stock_uom": stock_uom, "packaging_spec_level": level.level_name,
    } for i, level in enumerate(levels, 1)]
    order = frappe.get_doc({
        "doctype": "VAS Order", "handling_unit": handling_unit, "warehouse": hu.warehouse,
        "work_center_bin": work_center_bin, "priority": "Normal", "status": "Open", "activities": activities,
    })
    order.insert(ignore_permissions=True)
    return order.name
