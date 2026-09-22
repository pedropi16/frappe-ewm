import frappe
from frappe_wms.services.slotting import analyze_slotting as _analyze_slotting, generate_rearrangement_tasks as _generate_rearrangement_tasks

@frappe.whitelist()
def analyze_slotting(warehouse, from_date=None, to_date=None, min_picks=5):
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    return _analyze_slotting(warehouse, from_date, to_date, int(min_picks))

@frappe.whitelist()
def generate_rearrangement_tasks(warehouse, recommendations=None):
    if isinstance(recommendations, str):
        recommendations = frappe.parse_json(recommendations)
    return _generate_rearrangement_tasks(warehouse, recommendations)
