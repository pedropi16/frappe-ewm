import frappe
from frappe_wms.services import adhoc_tasks
from frappe_wms.services.locks import require_free_many
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.utils import parse_json


@frappe.whitelist()
@retry_on_deadlock
def create_adhoc_tasks(lines, destination_bin, priority="Normal"):
    """Internal Move tasks for the marked lines - each {handling_unit} (everything in it) or {name: WMS Stock Balance, quantity?}."""
    lines = parse_json(lines, "lines")
    require_free_many([("Handling Unit", l["handling_unit"]) if l.get("handling_unit") and not l.get("name") else ("WMS Stock Balance", l["name"]) for l in lines])
    return adhoc_tasks.create_adhoc_tasks(lines, destination_bin, priority)
