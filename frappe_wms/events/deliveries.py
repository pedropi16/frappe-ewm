import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.determination import determine_route

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
