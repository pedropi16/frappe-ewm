"""Run before a fresh scale_loadtest.cjs run, via:

    bench --site erp.pinohomelab.duckdns.org execute frappe_wms.tests.e2e.loadtest.reset_backlog.execute

Releases every Warehouse Order a previous run's loadtest personas (LOADTEST-RF*) left assigned to
themselves but not finished, back to the unassigned pool. This is NOT a web-exposed API on purpose
(require_role would need a far more privileged role than any RF resource should have to do a bulk
reassignment like this) - an Administrator/System Manager runs it directly via bench execute, same
as every other one-off maintenance script in this engagement.

Why this matters: a resource that personally accumulates hundreds of its own assigned-but-
unconfirmed tasks across many runs in one sitting can crowd its own "mine" results even after the
list_my_tasks/pullWork fixes (2026-10-02) - those fix the *global* and *per-Warehouse-Order*
crowding cases, but a resource holding e.g. 300 of its own half-finished Warehouse Orders is a
separate, narrower thing neither fix addresses (see PLAN.md). This does NOT touch confirmed
progress, cancel anything, or discard any already-posted stock movement - it only clears
assigned_resource (WO + its own non-terminal tasks) and resets the WO's status back to "Open" so
ANY eligible resource can pick the remaining work back up fresh next pull, same as if no one had
ever claimed it.
"""
import frappe


def execute():
    wos = frappe.get_all("Warehouse Order", filters={"assigned_resource": ["like", "LOADTEST-%"], "status": ["not in", ["Completed", "Cancelled"]]}, pluck="name")
    for wo in wos:
        frappe.db.set_value("Warehouse Order", wo, {"assigned_resource": None, "status": "Open"})
        frappe.db.sql("update `tabWarehouse Task` set assigned_resource=NULL where warehouse_order=%s and status not in ('Confirmed', 'Cancelled')", (wo,))
    frappe.db.commit()
    print(f"reset {len(wos)} warehouse order(s) previously assigned to loadtest resources")
