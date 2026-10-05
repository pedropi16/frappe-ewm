import frappe
from frappe_wms.services import production_supply as ps
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.utils import parse_json, require_wms_access


@frappe.whitelist()
@retry_on_deadlock
def list_psas(warehouse=None):
    require_wms_access()
    filters = {"active": 1, **({"warehouse": warehouse} if warehouse else {})}
    return frappe.get_list("Production Supply Area", filters=filters, fields=["name", "warehouse", "psa_name", "supply_bin"], order_by="name asc")


@frappe.whitelist()
@retry_on_deadlock
def staging_overview(psa):
    return ps.staging_overview(psa)


@frappe.whitelist()
@retry_on_deadlock
def stage_items(psa, method, lines):
    return ps.stage_items(psa, method, parse_json(lines, "lines"))


@frappe.whitelist()
@retry_on_deadlock
def close_pmr(pmr_name):
    return ps.close_pmr(pmr_name)
