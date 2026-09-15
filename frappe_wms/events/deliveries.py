import frappe
from frappe import _
from frappe.utils import flt

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
