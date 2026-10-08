"""ERPNext <-> WMS document flow - see services/erp_integration.py."""
import frappe

from frappe_wms.services import erp_integration as svc
from frappe_wms.services.concurrency import retry_on_deadlock


@frappe.whitelist()
@retry_on_deadlock
def complete_short(doctype, delivery_name, reason=None):
    if doctype not in ("Inbound Delivery", "Outbound Delivery"):
        frappe.throw(frappe._("Only Inbound and Outbound Deliveries can be completed short"))
    return svc.complete_short(doctype, delivery_name, reason)


@frappe.whitelist()
def wms_status(doctype, name):
    frappe.get_doc(doctype, name).check_permission("read")
    return svc.wms_status(doctype, name)


@frappe.whitelist()
@retry_on_deadlock
def retry_erp_posting(log_name):
    from frappe_wms.services.erp_sync_queue import retry_now
    return retry_now(log_name)


@frappe.whitelist()
@retry_on_deadlock
def split_delivery(delivery_name, lines, reason=None):
    from frappe_wms.services.delivery_split import split_outbound_delivery
    return split_outbound_delivery(delivery_name, frappe.parse_json(lines), reason)
