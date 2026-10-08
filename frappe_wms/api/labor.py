"""Labor shifts and indirect labor - see services/labor.py."""
import frappe

from frappe_wms.services import labor


@frappe.whitelist()
def start_indirect_labor(resource, activity):
    return labor.start_indirect(resource, activity)


@frappe.whitelist()
def stop_indirect_labor(resource):
    return labor.stop_indirect(resource)


@frappe.whitelist()
def labor_summary(warehouse, from_date, to_date):
    from frappe_wms.utils import require_wms_access
    require_wms_access()
    return labor.labor_summary(warehouse, from_date, to_date)
