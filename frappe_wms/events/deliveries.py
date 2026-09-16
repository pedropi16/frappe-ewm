import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.determination import determine_route
from frappe_wms.services.allocation import cancel_allocations_for_delivery
from frappe_wms.services.task import task_names_for_allocations

def _validate_lines(doc, quantity_field):
    if not doc.items:
        frappe.throw(_("At least one item is required"))
    seen = set()
    for row in doc.items:
        if flt(row.get(quantity_field)) <= 0:
            frappe.throw(_("Row {0}: quantity must be greater than zero").format(row.idx))
        if row.line_number in seen:
            frappe.throw(_("Duplicate line number {0}").format(row.line_number))
        seen.add(row.line_number)

def validate_inbound_delivery(doc, method=None):
    _validate_lines(doc, "expected_quantity")

def validate_outbound_delivery(doc, method=None):
    _validate_lines(doc, "requested_quantity")
    _apply_route_defaults(doc)

def _apply_route_defaults(doc):
    if not doc.route: doc.route = determine_route(doc.warehouse)
    if not doc.route: return
    route = frappe.get_cached_doc("WMS Route", doc.route)
    if not doc.staging_bin and route.default_staging_bin: doc.staging_bin = route.default_staging_bin
    if not doc.door and route.default_door: doc.door = route.default_door

def before_cancel_outbound_delivery(doc, method=None):
    if frappe.db.exists("Goods Issue", {"outbound_delivery": doc.name, "docstatus": 1}):
        frappe.throw(_("Cannot cancel: a Goods Issue is posted against this delivery. Reverse it first."))
    allocation_names = frappe.get_all("Stock Allocation", filters={"outbound_delivery": doc.name, "status": ["!=", "Cancelled"]}, pluck="name")
    task_names = list(task_names_for_allocations(allocation_names))
    if not task_names: return
    picked_tasks = frappe.get_all("Warehouse Task", filters={"name": ["in", task_names], "confirmed_quantity": [">", 0]}, pluck="name")
    if not picked_tasks: return
    # reverse_task() doesn't un-confirm the original task, it posts a compensating reversal task
    # instead - so a picked task that's already been reversed shouldn't still block cancellation.
    reversed_originals = set(frappe.get_all("Warehouse Task", filters={"reversal_of": ["in", picked_tasks]}, pluck="reversal_of"))
    if set(picked_tasks) - reversed_originals:
        frappe.throw(_("Cannot cancel: picking has already started on this delivery. Reverse the confirmed task(s) first."))

def on_cancel_outbound_delivery(doc, method=None):
    cancel_allocations_for_delivery(doc.name)
    doc.db_set("status", "Cancelled")
