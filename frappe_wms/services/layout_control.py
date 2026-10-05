"""Layout-oriented storage control (SAP EWM): goods that cannot go straight from a source to a destination - because of the
warehouse layout or the resources working in it - go through an intermediate storage bin. A Layout Storage Control rule names
the source and destination storage type/group it applies to and where the intermediate bin is.

The task for the first leg is created with the intermediate bin as its destination and the real one in final_destination_bin;
when it is fully confirmed the second leg is created from what actually arrived (advance), the same just-in-time chaining
a Storage Process uses.
"""
import frappe
from frappe import _
from frappe.utils import flt

from frappe_wms.services.bin_rules import bin_violations


def _bin_attrs(bin_name):
    return frappe.db.get_value("Storage Bin", bin_name, ["storage_type", "storage_group"], as_dict=True) or frappe._dict()


def match_rule(warehouse, source_bin, destination_bin):
    src, dst = _bin_attrs(source_bin), _bin_attrs(destination_bin)
    for rule in frappe.get_all("Layout Storage Control", filters={"warehouse": warehouse, "active": 1}, fields=["*"], order_by="priority asc, name asc"):
        if all(not want or want == have for want, have in (
                (rule.source_storage_type, src.storage_type), (rule.source_storage_group, src.storage_group),
                (rule.destination_storage_type, dst.storage_type), (rule.destination_storage_group, dst.storage_group))):
            return rule
    return None


def intermediate_bin(rule, task):
    if rule.intermediate_bin: return rule.intermediate_bin
    filters = {"warehouse": rule.warehouse, "storage_type": rule.intermediate_storage_type, "active": 1, "putaway_blocked": 0}
    if rule.intermediate_storage_group: filters["storage_group"] = rule.intermediate_storage_group
    if rule.intermediate_storage_section: filters["storage_section"] = rule.intermediate_storage_section
    hu_type = frappe.db.get_value("Handling Unit", task.source_hu, "hu_type") if task.source_hu else None
    for name in frappe.get_all("Storage Bin", filters=filters, pluck="name", order_by="sequence asc"):
        if not bin_violations(name, item=task.product, stock_type=task.stock_type_to, hu_type=hu_type, batch_no=task.batch_no, destination_hu=task.source_hu): return name
    frappe.throw(_("No usable intermediate bin for layout storage control rule {0}").format(rule.name))


def reroute(task):
    """Called on a new, unsaved task: send it to the intermediate bin when a rule applies."""
    if not (task.source_bin and task.destination_bin) or task.final_destination_bin: return
    rule = match_rule(task.warehouse, task.source_bin, task.destination_bin)
    if not rule: return
    inter = intermediate_bin(rule, task)
    if inter in (task.source_bin, task.destination_bin): return
    task.final_destination_bin = task.destination_bin
    task.destination_bin = inter


def advance(task):
    """The first leg is confirmed: create the leg from the intermediate bin to the final destination."""
    if not task.final_destination_bin: return None
    from frappe_wms.services.warehouse_order import attach_task
    leg = frappe.get_doc({"doctype": "Warehouse Task", "warehouse_request": task.warehouse_request, "task_type": task.task_type, "warehouse": task.warehouse,
        "product": task.product, "planned_quantity": flt(task.confirmed_quantity), "stock_uom": task.stock_uom, "batch_no": task.batch_no, "serial_no": task.serial_no,
        "source_bin": task.destination_bin, "source_hu": task.destination_hu or task.source_hu, "destination_bin": task.final_destination_bin,
        "stock_type_from": task.stock_type_to or task.stock_type_from, "stock_type_to": task.stock_type_to or task.stock_type_from,
        "movement_type": task.movement_type, "priority": task.priority or "Normal", "status": "Open", "predecessor_task": task.name})
    attach_task(leg, frappe.generate_hash(length=10), reference_doctype="Warehouse Task", reference_name=task.name)  # same activity/area, so it queues like the first leg
    leg.insert(ignore_permissions=True)
    return leg.name
