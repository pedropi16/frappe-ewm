"""Stock ledger archiving (SAP data archiving for warehouse documents).

The WMS Stock Ledger Entry table only ever grows. An archive run moves every entry posted before
its cutoff into a gzipped JSON-lines file attached to the WMS Ledger Archive Run, deletes them, and
posts one carry-forward entry (movement type 999) per stock position that still holds stock, dated
at the position's first receipt so FIFO/FEFO and rebuild_balances() see the same history. Ledger
totals therefore stay exactly equal to WMS Stock Balance, and nothing downstream changes.

WMS Settings > Ledger Archiving sets the retention (months, at least 12; 0 = never). The monthly
job archives whatever is older than that; supervisors can also start a run by hand.
"""
import gzip
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, add_to_date, cint, flt, get_datetime, getdate, now_datetime, nowdate

MIN_RETENTION_MONTHS = 12
CARRY_FORWARD = "999"
KEY = ("warehouse", "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type")
FIELDS = ["name", "posting_datetime", *KEY, "quantity", "stock_uom", "movement_type", "reference_doctype", "reference_name",
          "reference_line", "warehouse_task", "reversal_of", "idempotency_key", "posting_user", "posting_device", "creation"]


def _ensure_movement_type():
    if not frappe.db.exists("WMS Movement Type", CARRY_FORWARD):
        frappe.get_doc({"doctype": "WMS Movement Type", "movement_type_code": CARRY_FORWARD, "movement_type_name": "Archive Carry-Forward",
                        "movement_category": "Archive Carry-Forward", "inventory_effect": "No Quantity Change", "active": 1}).insert(ignore_permissions=True)


def latest_cutoff(warehouse):
    """The newest date before which this warehouse's ledger has been archived, if any."""
    rows = frappe.get_all("WMS Ledger Archive Run", filters={"status": "Completed", "warehouse": ["in", [warehouse, "", None]]},
                          pluck="cutoff_date", order_by="cutoff_date desc", limit=1)
    return getdate(rows[0]) if rows else None


def ensure_reversible(doc):
    """Refuses to reverse a document whose ledger entries were archived: the reversal would find
    nothing to reverse and quietly post nothing."""
    cutoff = latest_cutoff(doc.get("warehouse"))
    posted = doc.get("posting_datetime") or doc.get("posting_date") or doc.get("creation")
    if cutoff and posted and getdate(posted) < cutoff:
        frappe.throw(_("{0} {1} was posted before {2}, and the stock ledger up to that date is archived. Post a correcting movement instead of reversing it.")
                     .format(_(doc.doctype), doc.name, frappe.format(cutoff, "Date")), title=_("Archived"))


def start_run(cutoff_date, warehouse=None):
    """Supervisor action: queue an archive run."""
    from frappe_wms.utils import require_role
    require_role("WMS Administrator", "WMS Supervisor")
    cutoff = getdate(cutoff_date)
    if cutoff > getdate(add_months(nowdate(), -MIN_RETENTION_MONTHS)):
        frappe.throw(_("Entries younger than {0} months cannot be archived").format(MIN_RETENTION_MONTHS))
    run = frappe.get_doc({"doctype": "WMS Ledger Archive Run", "cutoff_date": cutoff, "warehouse": warehouse, "status": "Queued",
                          "started_by": frappe.session.user}).insert(ignore_permissions=True)
    frappe.enqueue("frappe_wms.services.archiving.execute_run", queue="long", timeout=6 * 3600, run_name=run.name, enqueue_after_commit=True)
    return run.name


def monthly_archive():
    """Scheduler (monthly): archives what is older than WMS Settings' retention."""
    months = cint(frappe.db.get_single_value("WMS Settings", "ledger_retention_months"))
    if not months: return
    months = max(months, MIN_RETENTION_MONTHS)
    cutoff = getdate(add_months(nowdate(), -months)).replace(day=1)
    if frappe.db.exists("WMS Ledger Archive Run", {"status": ["in", ["Queued", "Running", "Completed"]], "cutoff_date": [">=", cutoff],
                                                   "warehouse": ["in", ["", None]]}):
        return
    run = frappe.get_doc({"doctype": "WMS Ledger Archive Run", "cutoff_date": cutoff, "status": "Queued", "started_by": "Administrator"}).insert(ignore_permissions=True)
    execute_run(run.name)


def execute_run(run_name):
    run = frappe.get_doc("WMS Ledger Archive Run", run_name)
    if run.status not in ("Queued", "Failed"): return run.status
    run.db_set("status", "Running")
    if not frappe.flags.in_test: frappe.db.commit()
    frappe.db.savepoint("wms_archive")
    try:
        archived, carried = _archive(run)
    except Exception:
        frappe.db.rollback(save_point="wms_archive")
        frappe.db.set_value("WMS Ledger Archive Run", run_name, {"status": "Failed", "error": frappe.get_traceback()[-4000:]})
        if not frappe.flags.in_test: frappe.db.commit()
        raise
    run.db_set({"status": "Completed", "entries_archived": archived, "positions_carried_forward": carried, "completed_at": now_datetime(), "error": None})
    if not frappe.flags.in_test: frappe.db.commit()
    return "Completed"


def _archive(run):
    _ensure_movement_type()
    cutoff = get_datetime(run.cutoff_date)
    warehouses = [run.warehouse] if run.warehouse else frappe.get_all("WMS Warehouse", pluck="name")
    lines, archived, carried = [], 0, 0
    for warehouse in warehouses:
        # Everything for the warehouse in one transaction: carry-forwards are inserted before the
        # originals are deleted, so a failure rolls back to exactly where it started.
        rows = frappe.get_all("WMS Stock Ledger Entry", filters={"warehouse": warehouse, "posting_datetime": ["<", cutoff]},
                              fields=FIELDS, order_by="posting_datetime asc, creation asc")
        if not rows: continue
        totals, first_in, uom = defaultdict(float), {}, {}
        for r in rows:
            key = tuple(r.get(k) or None for k in KEY)
            totals[key] += flt(r.quantity)
            uom[key] = r.stock_uom
            if flt(r.quantity) > 0 and key not in first_in: first_in[key] = r.posting_datetime
            lines.append(json.dumps(r, default=str))
        for i, (key, qty) in enumerate(sorted(totals.items(), key=lambda kv: str(kv[0])), 1):
            if abs(qty) < 0.000001: continue
            sle = frappe.new_doc("WMS Stock Ledger Entry")
            sle.update(dict(zip(KEY, key)))
            sle.update({"quantity": qty, "stock_uom": uom[key], "movement_type": CARRY_FORWARD,
                        # dated at the position's first receipt so FIFO/FEFO and rebuilds keep their order
                        "posting_datetime": first_in.get(key) or add_to_date(cutoff, seconds=-1),
                        "reference_doctype": "WMS Ledger Archive Run", "reference_name": run.name,
                        "idempotency_key": f"ARC:{run.name}:{warehouse}:{i}", "posting_user": frappe.session.user})
            sle.flags.ignore_permissions = True
            sle.flags.ignore_links = True  # an archived HU/batch may since have been deleted
            sle.insert()
            carried += 1
        names = [r.name for r in rows]
        for chunk in range(0, len(names), 1000):
            frappe.db.delete("WMS Stock Ledger Entry", {"name": ["in", names[chunk:chunk + 1000]]})
        archived += len(rows)
    if lines:
        content = gzip.compress(("\n".join(lines) + "\n").encode())
        f = frappe.get_doc({"doctype": "File", "file_name": f"{run.name}-ledger.jsonl.gz", "is_private": 1, "content": content,
                            "attached_to_doctype": "WMS Ledger Archive Run", "attached_to_name": run.name, "attached_to_field": "archive_file"})
        f.flags.ignore_permissions = True
        f.insert()
        run.db_set("archive_file", f.file_url)
    return archived, carried
