import frappe
from frappe_wms.services import adhoc_tasks
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.utils import parse_json


@frappe.whitelist()
@retry_on_deadlock
def create_adhoc_tasks(lines, destination_bin, priority="Normal"):
    """Internal Move tasks for the marked lines - each {handling_unit} (everything in it) or {name: WMS Stock Balance, quantity?}."""
    return adhoc_tasks.create_adhoc_tasks(parse_json(lines, "lines"), destination_bin, priority)
