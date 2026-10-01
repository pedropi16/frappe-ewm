"""Posting WMS results to ERPNext - synchronously, or queued with retry.

WMS Warehouse > ERPNext Posting Mode:
  Synchronous        the ERPNext document posts inside the warehouse transaction; an ERPNext
                     error (closed period, missing account, negative stock in ERPNext) stops the
                     warehouse posting too. The default, and what every flow did before.
  Queued with Retry  the warehouse posts first; the ERPNext document follows in a background job
                     recorded in WMS ERP Sync Log, retried with backoff until it succeeds - SAP's
                     qRFC queue between EWM and ECC. The dock never stops for an accounting problem.

Every call site goes through dispatch(operation, doc, **payload). An operation writes the
resulting ERPNext document name(s) back onto the WMS document itself, so a queued run and an
inline run leave exactly the same trace.
"""
import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from frappe_wms.services import erpnext_sync

QUEUED = "Queued with Retry"
MAX_ATTEMPTS = 12
PENDING = ("Queued", "Running", "Failed")


# ------------------------------------------------------------------ operations

def _goods_receipt(doc):
    if doc.docstatus != 1: return  # cancelled before its turn came
    erpnext_sync.sync_goods_receipt(doc)


def _goods_issue(doc):
    if doc.docstatus != 1: return
    erpnext_sync.sync_goods_issue(doc)


def _posting_change(doc):
    se = erpnext_sync.sync_posting_change(doc)
    if se: doc.db_set("erpnext_stock_entry", se, update_modified=False)


def _quality_inspection(doc):
    from frappe_wms.services.quality import _create_erpnext_quality_inspection
    if not doc.get("erpnext_quality_inspection"):
        qi = _create_erpnext_quality_inspection(doc, doc.failed_quantity)
        if qi: doc.db_set("erpnext_quality_inspection", qi, update_modified=False)
    if not doc.get("erpnext_stock_entry"):
        se = erpnext_sync.sync_quality_inspection(doc, doc.passed_quantity, doc.failed_quantity)
        if se: doc.db_set("erpnext_stock_entry", se, update_modified=False)


def _kitting_order(doc, consumed=None):
    if doc.get("erpnext_stock_entry"): return
    se = erpnext_sync.sync_kitting_order(doc, consumed=consumed)
    if se: doc.db_set("erpnext_stock_entry", se, update_modified=False)


def _work_order_transfer(doc):
    erpnext_sync.sync_work_order_material_transfer(doc)


def _over_difference(doc):
    if doc.get("erpnext_stock_entry"): return
    erpnext_sync.sync_over_difference(doc)


def _count_rows(doc, rows=None):
    rows = [r for r in doc.items if r.name in set(rows or [])] if rows is not None else None
    gain, loss = erpnext_sync.sync_physical_inventory_count(doc, rows=rows)
    join = lambda current, new: ", ".join(x for x in (current, new) if x)  # noqa: E731
    current = frappe.db.get_value(doc.doctype, doc.name, ["erpnext_gain_stock_entry", "erpnext_loss_stock_entry"], as_dict=True)
    updates = {}
    if gain: updates["erpnext_gain_stock_entry"] = join(current.erpnext_gain_stock_entry, gain)
    if loss: updates["erpnext_loss_stock_entry"] = join(current.erpnext_loss_stock_entry, loss)
    if updates:
        doc.db_set(updates, update_modified=False)


OPERATIONS = {
    "goods_receipt": _goods_receipt,
    "goods_receipt_reversal": erpnext_sync.reverse_goods_receipt,
    "goods_issue": _goods_issue,
    "goods_issue_reversal": erpnext_sync.reverse_goods_issue,
    "posting_change": _posting_change,
    "quality_inspection": _quality_inspection,
    "kitting_order": _kitting_order,
    "work_order_transfer": _work_order_transfer,
    "over_difference": _over_difference,
    "count_rows": _count_rows,
}
# A reversal whose forward posting never reached ERPNext has nothing to undo there.
REVERSES = {"goods_receipt_reversal": "goods_receipt", "goods_issue_reversal": "goods_issue"}


# ------------------------------------------------------------------ dispatch

def mode(warehouse):
    return (frappe.db.get_value("WMS Warehouse", warehouse, "erp_sync_mode") if warehouse else None) or "Synchronous"


def dispatch(operation, doc, **payload):
    if operation not in OPERATIONS: frappe.throw(_("Unknown ERPNext posting operation {0}").format(operation))
    if frappe.flags.get("wms_erp_sync_job") or mode(doc.get("warehouse")) != QUEUED:
        return OPERATIONS[operation](doc, **payload)
    forward = REVERSES.get(operation)
    if forward:
        pending = frappe.get_all("WMS ERP Sync Log", filters={"reference_doctype": doc.doctype, "reference_name": doc.name,
                                                              "operation": forward, "status": ["in", PENDING]}, pluck="name")
        if pending:
            for name in pending:
                frappe.db.set_value("WMS ERP Sync Log", name, {"status": "Cancelled", "last_error": _("Reversed in the warehouse before it reached ERPNext")})
            return None
    log = frappe.get_doc({"doctype": "WMS ERP Sync Log", "operation": operation, "status": "Queued", "warehouse": doc.get("warehouse"),
                         "reference_doctype": doc.doctype, "reference_name": doc.name, "payload": json.dumps(payload) if payload else None})
    log.insert(ignore_permissions=True)
    frappe.enqueue("frappe_wms.services.erp_sync_queue.run", queue="short", log_name=log.name, enqueue_after_commit=True)
    return None


def run(log_name):
    """Runs one queued posting. Commits its own outcome (success or the failure record)."""
    log = frappe.get_doc("WMS ERP Sync Log", log_name, for_update=True)
    if log.status not in ("Queued", "Failed"): return log.status
    # Earlier postings for the same WMS document go first (a reversal never overtakes its sync).
    earlier = frappe.db.sql("""select name from `tabWMS ERP Sync Log` where reference_doctype=%s and reference_name=%s
        and status in ('Queued','Failed') and creation < %s limit 1""", (log.reference_doctype, log.reference_name, log.creation))
    if earlier:
        log.db_set({"next_retry_at": add_to_date(now_datetime(), minutes=2)}, update_modified=False)
        frappe.db.commit()
        return "Waiting"
    doc = frappe.get_doc(log.reference_doctype, log.reference_name)
    payload = json.loads(log.payload) if log.payload else {}
    frappe.db.savepoint("wms_erp_sync")
    previous = frappe.flags.get("wms_erp_sync_job")
    frappe.flags.wms_erp_sync_job = True
    try:
        OPERATIONS[log.operation](doc, **payload)
    except Exception:
        frappe.db.rollback(save_point="wms_erp_sync")
        attempts = cint(log.attempts) + 1
        log.db_set({"status": "Failed", "attempts": attempts, "last_error": frappe.get_traceback(with_context=False)[-4000:],
                    "next_retry_at": add_to_date(now_datetime(), minutes=min(5 * 2 ** (attempts - 1), 360))}, update_modified=True)
        frappe.clear_messages()
    else:
        log.db_set({"status": "Done", "attempts": cint(log.attempts) + 1, "completed_at": now_datetime(), "last_error": None}, update_modified=True)
    finally:
        frappe.flags.wms_erp_sync_job = previous
    if not frappe.flags.in_test:
        frappe.db.commit()
    return log.status


def retry_due():
    """Scheduler: failed postings whose backoff has passed, and queued ones whose job was lost."""
    stale_queued = add_to_date(now_datetime(), minutes=-10)
    rows = frappe.db.sql("""select name from `tabWMS ERP Sync Log`
        where (status='Failed' and attempts < %s and (next_retry_at is null or next_retry_at <= %s))
           or (status='Queued' and creation <= %s)
           or (status='Running' and modified <= %s)
        order by creation asc limit 200""", (MAX_ATTEMPTS, now_datetime(), stale_queued, stale_queued), as_dict=True)
    for r in rows:
        if frappe.db.get_value("WMS ERP Sync Log", r.name, "status") == "Running":
            frappe.db.set_value("WMS ERP Sync Log", r.name, "status", "Failed")
        run(r.name)


def retry_now(log_name):
    from frappe_wms.utils import require_role
    require_role("WMS Supervisor", "WMS Administrator")
    status = frappe.db.get_value("WMS ERP Sync Log", log_name, "status")
    if status not in ("Failed", "Queued"): frappe.throw(_("Only a failed or queued posting can be retried"))
    frappe.db.set_value("WMS ERP Sync Log", log_name, {"status": "Failed", "next_retry_at": None})
    return run(log_name)


def open_problems(warehouse=None, limit=50):
    filters = {"status": ["in", ["Failed", "Queued"]]}
    if warehouse: filters["warehouse"] = warehouse
    return frappe.get_all("WMS ERP Sync Log", filters=filters, fields=["name", "operation", "status", "reference_doctype", "reference_name",
                          "attempts", "next_retry_at", "last_error", "creation"], order_by="creation asc", limit=limit)
