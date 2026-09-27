import frappe
from frappe_wms.services.deconsolidation import create_deconsolidation_tasks as _create_deconsolidation_tasks
from frappe_wms.utils import parse_json
from frappe_wms.services.idempotency import run_once


@frappe.whitelist()
def create_deconsolidation_tasks(source_hu, lines, idempotency_key=None):
    return run_once(idempotency_key, lambda: _create_deconsolidation_tasks(source_hu, parse_json(lines, "lines")))
