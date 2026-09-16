import frappe
from frappe_wms.services.deconsolidation import create_deconsolidation_tasks as _create_deconsolidation_tasks
from frappe_wms.utils import parse_json


@frappe.whitelist()
def create_deconsolidation_tasks(source_hu, lines):
    return _create_deconsolidation_tasks(source_hu, parse_json(lines, "lines"))
