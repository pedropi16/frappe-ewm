import contextlib
import functools
import random
import time

import frappe

# How many times one API call is attempted in total before a deadlock is surfaced to the caller.
ATTEMPTS = 4


@contextlib.contextmanager
def latest_committed_locks():
    """MariaDB 11.6+ defaults to innodb_snapshot_isolation=ON: a locking read that has to wait for a hot row fails with error 1020 "Record has changed since last read"
    once the other transaction commits, because the row is newer than this transaction's first read. For WMS's own serialising locks (a bin's Handling Units, the
    Goods Receipt naming counter) that is exactly the contention they exist for - found under load as 31 of 33 HTTP 500s. Inside this block a locking read sees the
    latest committed row instead. Use it ONLY around WMS's own lock reads: left off globally, ERPNext's non-locking Bin update loses updates (reproduced: Bin
    under-counted by 89-120 units), the 1020 is what makes that path fail and retry."""
    try: frappe.db.sql("set session innodb_snapshot_isolation=0")
    except Exception: yield; return  # older server without the variable
    try: yield
    finally: frappe.db.sql("set session innodb_snapshot_isolation=1")


def insert_hot(doc):
    """Insert a document whose naming series counter is a hot lock row (tasks, warehouse orders, requests, receipts)."""
    with latest_committed_locks():
        doc.insert(ignore_permissions=True)
    return doc


def retry_on_deadlock(fn):
    """Re-run a whole whitelisted API call from scratch when the database aborts it as a deadlock
    victim (or a lock wait times out).

    Every RF/desk action here is one request = one transaction, and a warehouse at real volume
    runs many of them against the same hot rows at once (the same receiving bin's balances, the
    same Warehouse Order, the same naming-series counters). InnoDB resolves a deadlock by rolling
    the victim's *entire* transaction back - reproduced under a 26-user simulated shift as raw
    HTTP 500 QueryDeadlockErrors out of Goods Receipt, task confirmation, HU loading and Warehouse
    Order pulls. Nothing the request wrote survives that, so the only correct recovery is to
    roll back and run the whole call again - which is exactly what a person tapping "Retry" on
    the scanner would do, minus the person.

    Retrying just the statement (or one inner function, as services/stock.py used to try) is
    NOT safe: the rest of the request's earlier writes are already gone, so the retried part
    would commit on its own, e.g. ledger rows for a Goods Receipt that no longer exists.

    Only the outermost decorated call retries (an API function calling another decorated one
    just lets the exception propagate to the outer retry), and nothing retries under tests,
    where a rollback would also discard the test's own fixtures.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if getattr(frappe.local, "wms_retry_active", False) or frappe.flags.in_test:
            return fn(*args, **kwargs)
        frappe.local.wms_retry_active = True
        try:
            for attempt in range(ATTEMPTS):
                try:
                    return fn(*args, **kwargs)
                except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
                    if attempt == ATTEMPTS - 1:
                        raise
                    frappe.db.rollback()
                    # Messages queued by the abandoned attempt (msgprint/throw side effects)
                    # describe work that no longer exists.
                    frappe.local.message_log = []
                    # Jittered backoff so the transactions that collided don't collide again.
                    time.sleep(random.uniform(0.05, 0.2) * (2 ** attempt))
        finally:
            frappe.local.wms_retry_active = False
    return wrapper
