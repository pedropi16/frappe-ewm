import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.labeling import generate_sscc as _generate_sscc, render_hu_label_zpl as _render_hu_label_zpl
from frappe_wms.utils import require_role

LABEL_ROLES = ("WMS Operator", "WMS Packer", "WMS Supervisor", "WMS Process Engineer")


@frappe.whitelist()
@retry_on_deadlock
def generate_sscc(hu_name):
    require_role(*LABEL_ROLES)
    return _generate_sscc(hu_name)


@frappe.whitelist()
@retry_on_deadlock
def render_hu_label_zpl(hu_name):
    require_role(*LABEL_ROLES)
    return _render_hu_label_zpl(hu_name)
