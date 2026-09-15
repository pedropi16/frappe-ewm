import frappe
from frappe import _
from frappe.utils import flt

def validate_task(doc, method=None):
    if doc.planned_quantity and flt(doc.planned_quantity) <= 0:
        frappe.throw(_("Planned quantity must be greater than zero"))
    if flt(doc.confirmed_quantity) < 0:
        frappe.throw(_("Confirmed quantity cannot be negative"))
    if doc.source_bin and doc.destination_bin and doc.source_bin == doc.destination_bin and doc.task_type != "Posting Change":
        frappe.throw(_("Source and destination bins must differ"))
    for field in ("source_bin", "destination_bin"):
        if doc.get(field) and frappe.db.get_value("Storage Bin", doc.get(field), "warehouse") != doc.warehouse:
            frappe.throw(_("Task bin does not belong to the task warehouse"))

def prevent_direct_cancel_after_posting(doc, method=None):
    if frappe.db.exists("WMS Stock Ledger Entry", {"warehouse_task": doc.name}):
        frappe.throw(_("Reverse the warehouse task through the WMS reversal service"))
