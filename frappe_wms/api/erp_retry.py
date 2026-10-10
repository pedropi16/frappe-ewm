import frappe
from frappe.client import submit as _submit
from frappe.desk.form.save import savedocs as _savedocs

from frappe_wms.services.concurrency import retry_on_deadlock

# ERPNext's own Submit (API and desk form) updates the same hot Bin / Stock Ledger rows the warehouse floor posts against and has no deadlock
# retry of its own: under load it surfaced as HTTP 500 QueryDeadlockError. The whole request is re-run, exactly as for the WMS endpoints.


@frappe.whitelist(methods=["POST", "PUT"])
@retry_on_deadlock
def submit(doc):
    return _submit(doc)


@frappe.whitelist(methods=["POST", "PUT"])
@retry_on_deadlock
def savedocs(doc: str, action: str):
    return _savedocs(doc, action)
