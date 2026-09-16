import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.task import my_resource
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
    row.db_set({"completed": 1, "completed_by": frappe.session.user, "completed_at": now_datetime(), "remarks": remarks}, update_modified=True)

    rows = frappe.get_all("VAS Order Activity", filters={"parent": order.name}, fields=["completed"])
    all_done = bool(rows) and all(r.completed for r in rows)
    updates = {"status": "Completed" if all_done else "In Process"}
    if all_done: updates.update({"completed_at": now_datetime(), "completed_by": frappe.session.user})
    order.db_set(updates, update_modified=True)
    return {"vas_order": order.name, "status": updates["status"]}
