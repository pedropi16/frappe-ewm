import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.stock import transfer_stock
from frappe_wms.services import erpnext_sync
from frappe_wms.utils import require_role

# SAP EWM's posting change: a stock-type-only move (AVAILABLE <-> WAREHOUSE_BLOCKED, a manual
# release after a recall hold, etc.) with no physical relocation - same bin/HU, just a different
# status. Quality Inspection already does exactly this shape for its own specific case (pass/
# fail routing); this generalizes it into a standalone, auditable document for every other
# reason an operator needs to change a stock type without a Quality Inspection to hang it on.

POSTING_CHANGE_ROLES = ("WMS Supervisor", "WMS Inventory Controller")


def post_posting_change(name):
    require_role(*POSTING_CHANGE_ROLES)
    doc = frappe.get_doc("WMS Posting Change", name, for_update=True)
    if doc.status != "Draft":
        frappe.throw(_("This posting change has already been posted or cancelled"))
    if doc.from_stock_type == doc.to_stock_type:
        frappe.throw(_("From and To stock type must differ"))

    # No validate_destination_bin call here, matching the existing Quality Inspection precedent
    # for this exact operation shape (services/quality.py::complete_inspection): the bin's own
    # product/stock-type mixing rules are about what's allowed to newly ARRIVE in a bin, not
    # about stock already resident there changing its own status in place - a bin holding only
    # AVAILABLE stock rejecting its own posting change to WAREHOUSE_BLOCKED (a real bin_violations
    # false positive, reproduced against the dev bench) would make posting changes unusable on
    # any storage type that doesn't explicitly allow mixed stock types.
    base = {"warehouse": doc.warehouse, "product": doc.product, "batch_no": doc.batch_no, "serial_no": doc.serial_no,
        "handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_uom": doc.stock_uom}
    source = {**base, "stock_type": doc.from_stock_type}
    destination = {"handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_type": doc.to_stock_type}
    transfer_stock(source=source, destination=destination, quantity=doc.quantity, movement_type="501",
        reference_doctype=doc.doctype, reference_name=doc.name, idempotency_key=f"PSC:{doc.name}")

    erpnext_se = erpnext_sync.sync_posting_change(doc)
    doc.db_set({
        "status": "Posted", "posted_by": frappe.session.user, "posted_at": now_datetime(),
        "erpnext_stock_entry": erpnext_se or "",
    }, update_modified=True)
    return {"posting_change": doc.name, "status": "Posted", "erpnext_stock_entry": erpnext_se}


def cancel_posting_change(name):
    require_role(*POSTING_CHANGE_ROLES)
    doc = frappe.get_doc("WMS Posting Change", name, for_update=True)
    if doc.status != "Posted":
        frappe.throw(_("Only a posted posting change can be cancelled"))
    base = {"warehouse": doc.warehouse, "product": doc.product, "batch_no": doc.batch_no, "serial_no": doc.serial_no,
        "handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_uom": doc.stock_uom}
    # Reverse: move the quantity back from to_stock_type to from_stock_type.
    transfer_stock(source={**base, "stock_type": doc.to_stock_type},
        destination={"handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_type": doc.from_stock_type},
        quantity=doc.quantity, movement_type="501", reference_doctype=doc.doctype, reference_name=doc.name,
        idempotency_key=f"PSC-REV:{doc.name}")
    if doc.erpnext_stock_entry:
        se = frappe.get_doc("Stock Entry", doc.erpnext_stock_entry)
        if se.docstatus == 1:
            se.flags.ignore_permissions = True
            se.cancel()
    doc.db_set({"status": "Cancelled"}, update_modified=True)
    return {"posting_change": doc.name, "status": "Cancelled"}
