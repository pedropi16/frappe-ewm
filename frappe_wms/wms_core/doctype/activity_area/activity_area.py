import frappe
from frappe.model.document import Document


class ActivityArea(Document):
    pass


@frappe.whitelist()
def generate_walk_path(area, activity):
    from frappe_wms.services.travel import generate_walk_path as _generate
    from frappe_wms.utils import require_role
    require_role("WMS Process Engineer", "WMS Supervisor", "WMS Administrator")
    return _generate(area, activity)
