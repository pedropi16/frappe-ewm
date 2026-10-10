"""Delivery worklist (SAP /SCWM/PRDO, /SCWM/PRDI with several hits): list rows with the statuses, and processing of the marked deliveries one by one."""
import frappe
from frappe import _
from frappe.utils import cint

from frappe_wms.services.locks import require_free

COLUMNS = {
    "Outbound Delivery": "d.outbound_delivery_number as number, d.customer as partner, d.delivery_date as date, d.priority, d.route, d.staging_bin, d.door, "
                         "d.allocation_status, d.picking_status, d.packing_status, d.loading_status, d.goods_issue_status",
    "Inbound Delivery": "d.inbound_delivery_number as number, d.supplier as partner, d.expected_arrival as date, d.receiving_bin, d.receipt_status, d.process_status, d.yard_status",
}
# fields the Mass Change may set on a released delivery (the master data of the screen, not its statuses)
CHANGEABLE = {"Outbound Delivery": ("priority", "route", "staging_bin", "door", "delivery_date"), "Inbound Delivery": ("receiving_bin", "expected_arrival")}


def rows(doctype, names):
    if not names: return []
    return frappe.db.sql(f"""select d.name, d.warehouse, d.status, d.docstatus, d.external_reference, d.closed_short, {COLUMNS[doctype]}
        from `tab{doctype}` d where d.name in %(n)s order by d.creation desc""", {"n": tuple(names)}, as_dict=True)


def _action(doctype, action, name, values):
    from frappe_wms.api import inbound, outbound
    if action == "change":
        require_free(doctype, name)
        values = {k: v for k, v in (values or {}).items() if k in CHANGEABLE[doctype] and v}
        if not values: frappe.throw(_("Nothing to change"))
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus != 1 or doc.status in ("Completed", "Cancelled"): frappe.throw(_("{0} is {1} and cannot be changed").format(name, doc.status))
        for k, v in values.items(): doc.db_set(k, v)
        doc.add_comment("Info", _("Changed by mass change: {0}").format(", ".join(f"{k} = {v}" for k, v in values.items())))
        return _("changed")
    fn = {("Outbound Delivery", "allocate"): lambda: outbound.allocate_delivery(name), ("Outbound Delivery", "pick"): lambda: outbound.create_pick_tasks(name),
          ("Outbound Delivery", "cartons"): lambda: outbound.plan_cartons(name), ("Outbound Delivery", "issue"): lambda: outbound.post_goods_issue_for_delivery(name),
          ("Inbound Delivery", "putaway"): lambda: inbound.plan_open_putaway(name, (values or {}).get("destination_bin"))}.get((doctype, action))
    if not fn: frappe.throw(_("Unknown action {0}").format(action))
    fn()
    return _("done")


def process(doctype, action, names, values=None):
    """Runs the action on each delivery on its own: a locked or refused delivery does not stop the others. -> {"done": [{name, text}], "errors": [{name, error}]}"""
    from frappe_wms.services.packing_center import _fail_text
    out = {"done": [], "errors": []}
    for i, name in enumerate(names):
        savepoint = f"dw_{i}"
        frappe.db.savepoint(savepoint)
        try:
            out["done"].append({"name": name, "text": _action(doctype, action, name, values)})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            frappe.db.rollback(save_point=savepoint)
            out["errors"].append({"name": name, "error": _fail_text(e)})
    return out
