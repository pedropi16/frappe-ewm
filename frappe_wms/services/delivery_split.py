"""SAP EWM delivery split: open quantity of an Outbound Delivery moves to a new delivery that is
processed on its own (own wave, route, truck). A delivery replicated from a draft Delivery Note
splits that draft too - the new delivery gets its own draft Delivery Note, posted by its own goods
issue. A Sales-Order-sourced delivery needs no draft: each goods issue makes its own Delivery Note."""
import frappe
from frappe import _
from frappe.utils import flt

from frappe_wms.services import erp_integration as erp
from frappe_wms.utils import require_role

EPS = 1e-9
_HEADER_SKIP = {"name", "items", "docstatus", "status", "amended_from", "creation", "modified", "modified_by", "owner", "idx", "doctype",
                "allocation_status", "picking_status", "packing_status", "loading_status", "goods_issue_status", "delivery_request",
                "pick_pack_pass_hu", "closed_short", "close_reason", "_user_tags", "_comments", "_assign", "_liked_by", "_seen"}
_LINE_SKIP = {"name", "parent", "parentfield", "parenttype", "idx", "doctype", "creation", "modified", "modified_by", "owner", "docstatus",
              "line_number", "allocated_quantity", "picked_quantity", "packed_quantity", "issued_quantity", "status"}


def movable_quantity(row):
    """What no warehouse step has touched yet on a delivery line."""
    return flt(row.requested_quantity) - max(flt(row.allocated_quantity), flt(row.picked_quantity), flt(row.packed_quantity), flt(row.issued_quantity))


def split_outbound_delivery(delivery_name, lines, reason=None):
    """lines: [{"line": <Outbound Delivery Item name>, "quantity": <stock-UOM qty to move>}]"""
    require_role("WMS Supervisor", "WMS Administrator")
    d = frappe.get_doc("Outbound Delivery", delivery_name, for_update=True)
    if d.docstatus != 1 or d.closed_short or d.status in ("Completed", "Cancelled", "Goods Issued"):
        frappe.throw(_("Only an open, released delivery can be split"))
    rows = {r.name: r for r in d.items}
    moves = {}
    for l in lines:
        row = rows.get(l.get("line"))
        qty = flt(l.get("quantity"))
        if not row or qty <= EPS: frappe.throw(_("Give an existing delivery line and a positive quantity"))
        moves[row.name] = moves.get(row.name, 0) + qty
    for name, qty in moves.items():
        if qty - movable_quantity(rows[name]) > EPS:
            frappe.throw(_("Line {0} ({1}): only {2} can be split off - the rest is already allocated, picked or issued.")
                         .format(rows[name].line_number, rows[name].item, movable_quantity(rows[name])), title=_("Delivery Split"))
    if all(abs(flt(r.requested_quantity) - moves.get(r.name, 0)) <= EPS for r in d.items):
        frappe.throw(_("Everything would move; a split must leave quantity on {0}").format(d.name))

    wh = frappe._dict(frappe.db.get_value("WMS Warehouse", d.warehouse, ["name", "outbound_follow_up"], as_dict=True), release_replicated_deliveries=1)
    previous, frappe.flags.wms_posting = frappe.flags.get("wms_posting"), True
    try:
        new_dn = _split_draft_note(d, rows, moves)
        header = {k: v for k, v in d.as_dict().items() if k not in _HEADER_SKIP}
        count = frappe.db.count("Outbound Delivery", {"outbound_delivery_number": ["like", f"{d.outbound_delivery_number}-S%"]})
        header["outbound_delivery_number"] = f"{d.outbound_delivery_number}-S{count + 1}"
        if new_dn: header.update(erp_source_name=new_dn.name)
        new_lines = []
        for name, qty in moves.items():
            line = {k: v for k, v in rows[name].as_dict().items() if k not in _LINE_SKIP}
            line["requested_quantity"] = qty
            if new_dn: line["source_document_line"] = new_dn.row_of[name]
            new_lines.append(line)
        new = erp._delivery_doc(erp.OUTBOUND, wh, header, new_lines)
        erp._release(new, wh)
        for name, qty in moves.items():
            left = flt(rows[name].requested_quantity) - qty
            if left <= EPS: frappe.db.delete("Outbound Delivery Item", {"name": name})
            else: frappe.db.set_value("Outbound Delivery Item", name, "requested_quantity", left)
        _refresh_allocation_status(d.name)
        if new_dn: frappe.db.set_value("Delivery Note", new_dn.name, "wms_outbound_delivery", new.name, update_modified=False)
    finally:
        frappe.flags.wms_posting = previous
    note = _("Split: {0} line(s) moved to {1}{2}").format(len(moves), new.name, f" - {reason}" if reason else "")
    d.add_comment("Comment", note)
    new.add_comment("Comment", _("Split off {0}{1}").format(d.name, f" - {reason}" if reason else ""))
    return {"source": d.name, "new": new.name, "delivery_note": new_dn.name if new_dn else None}


def _split_draft_note(d, rows, moves):
    """Draft Delivery Note of a replicated delivery -> a second draft carrying the moved rows."""
    if d.erp_source_doctype != "Delivery Note" or not d.erp_source_name: return None
    if frappe.db.get_value("Delivery Note", d.erp_source_name, "docstatus") != 0: return None
    src = frappe.get_doc("Delivery Note", d.erp_source_name)
    new = frappe.copy_doc(src)
    new.set("items", [])
    new.wms_outbound_delivery = None
    row_of, keep = {}, []
    for r in src.items:
        line = next((n for n in moves if rows[n].source_document_line == r.name), None)
        if not line:
            keep.append(r)
            continue
        factor = flt(r.conversion_factor or 1)
        moved = r.as_dict()
        moved.update(name=None, qty=moves[line] / factor, stock_qty=moves[line])
        row_of[line] = new.append("items", {k: v for k, v in moved.items() if k not in ("name", "parent", "idx", "creation", "modified")})
        left = flt(r.stock_qty) - moves[line]
        if left > EPS:
            r.qty, r.stock_qty = left / factor, left
            keep.append(r)
    src.set("items", keep)
    src.flags.ignore_permissions = new.flags.ignore_permissions = True
    src.save()
    new.insert()
    new.row_of = {line: new.items[i].name for i, line in enumerate(row_of)}
    return new


def _refresh_allocation_status(name):
    items = frappe.get_all("Outbound Delivery Item", {"parent": name}, ["requested_quantity", "allocated_quantity"])
    if all(flt(i.allocated_quantity) >= flt(i.requested_quantity) for i in items): status = "Fully Allocated"
    elif any(flt(i.allocated_quantity) > 0 for i in items): status = "Partially Allocated"
    else: status = "Not Allocated"
    frappe.db.set_value("Outbound Delivery", name, "allocation_status", status)
