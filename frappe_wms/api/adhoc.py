import frappe
from frappe_wms.services import adhoc_tasks
from frappe_wms.services.locks import require_free_many
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.utils import parse_json


@frappe.whitelist()
@retry_on_deadlock
def create_adhoc_tasks(lines, destination_bin=None, priority="Normal", process_type=None, reason=None, confirm=0):
    """Internal Move tasks for the marked lines - each {handling_unit} (everything in it) or {name: WMS Stock Balance, quantity?}."""
    lines = parse_json(lines, "lines")
    require_free_many([("Handling Unit", l["handling_unit"]) if l.get("handling_unit") and not l.get("name") else ("WMS Stock Balance", l["name"]) for l in lines])
    return adhoc_tasks.create_adhoc_tasks(lines, destination_bin, priority, process_type, reason, confirm)


@frappe.whitelist()
@retry_on_deadlock
def process_lines(lines, defaults=None):
    """Create of the ad hoc worklist: each line {handling_unit | name+quantity, destination_bin, process_type, priority, reason, confirm}; a refused line does not stop the others."""
    return adhoc_tasks.process_lines(parse_json(lines, "lines"), parse_json(defaults, "defaults") if defaults else {})


@frappe.whitelist()
def find_rows(warehouse, mode, by="handling_unit", value=None, names=None):
    return adhoc_tasks.find_rows(warehouse, mode, by, value, parse_json(names, "names") if names else None)


@frappe.whitelist()
def hu_content(handling_unit):
    return adhoc_tasks.hu_content(handling_unit)


@frappe.whitelist()
def hu_master(handling_unit):
    return adhoc_tasks.hu_master(handling_unit)


@frappe.whitelist()
def task_status(names):
    return adhoc_tasks.task_status(parse_json(names, "names"))


@frappe.whitelist()
def check_lines(lines, defaults=None):
    """Enter on the worklist: what each line would do (process type, destination bin / storage type / section) or why it is refused - nothing is created."""
    return adhoc_tasks.check_lines(parse_json(lines, "lines"), parse_json(defaults, "defaults") if defaults else {})
