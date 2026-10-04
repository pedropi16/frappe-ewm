import frappe
from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.batch_characteristics import (
    set_batch_characteristics as _set_batch_characteristics,
    get_batch_characteristics as _get_batch_characteristics,
)


@frappe.whitelist()
@retry_on_deadlock
def set_batch_characteristics(batch_no, values):
    if isinstance(values, str):
        values = frappe.parse_json(values)
    return _set_batch_characteristics(batch_no, values)


@frappe.whitelist()
@retry_on_deadlock
def get_batch_characteristics(batch_no):
    from frappe_wms.utils import require_wms_access
    require_wms_access()
    return _get_batch_characteristics(batch_no)
