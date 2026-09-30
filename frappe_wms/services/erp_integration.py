"""ERPNext <-> WMS document flow, the SAP ECC -> EWM delivery replication model.

ERPNext owns the business documents (orders, valuation, accounting); a WMS Warehouse executes
them. Per WMS Warehouse (section "ERP Integration"):

  inbound_replication   Manual | Purchase Order Submitted | Purchase Receipt Draft
  outbound_replication  Manual | Sales Order Submitted    | Delivery Note Draft
  release_replicated_deliveries, outbound_follow_up, erp_change_policy, close_short_orders

Replication (ERPNext -> WMS): a submitted order, or a draft Purchase Receipt / Delivery Note
(the ASN / ECC delivery), becomes an Inbound / Outbound Delivery for every order line whose
ERPNext warehouse is a WMS warehouse with that trigger. One ERPNext document can split into
several WMS deliveries, one per WMS warehouse - like ECC splitting a delivery per plant.

Changes (ERPNext -> WMS): editing ("Update Items"), cancelling or deleting the source document
follows it into the WMS deliveries while they have not started - otherwise the change is
refused, the way ECC refuses to change a delivery that is already in warehouse execution.

Report back (WMS -> ERPNext): goods receipt / goods issue post the source Purchase Receipt /
Delivery Note itself when the delivery came from one (services/erpnext_sync.py), and a delivery
completed short can close the order remainder in ERPNext.
"""
import frappe
from frappe import _
from frappe.utils import flt, nowdate

EPS = 0.000001

TRIGGERS = {
    "Purchase Order": ("inbound_replication", "Purchase Order Submitted"),
    "Purchase Receipt": ("inbound_replication", "Purchase Receipt Draft"),
    "Sales Order": ("outbound_replication", "Sales Order Submitted"),
    "Delivery Note": ("outbound_replication", "Delivery Note Draft"),
}
INBOUND = {"doctype": "Inbound Delivery", "item": "Inbound Delivery Item", "qty": "expected_quantity", "done": "received_quantity",
           "order_field": "purchase_order", "order_item_field": "purchase_order_item"}
OUTBOUND = {"doctype": "Outbound Delivery", "item": "Outbound Delivery Item", "qty": "requested_quantity", "done": "issued_quantity",
            "order_field": "sales_order", "order_item_field": "sales_order_item"}
SIDE = {"Purchase Order": INBOUND, "Purchase Receipt": INBOUND, "Sales Order": OUTBOUND, "Delivery Note": OUTBOUND}
ORDER_OF = {"Purchase Receipt": "Purchase Order", "Delivery Note": "Sales Order"}


# ------------------------------------------------------------------ configuration

def wms_warehouse_for(erp_warehouse):
    if not erp_warehouse: return None
    return frappe.db.get_value("WMS Warehouse", {"erpnext_warehouse": erp_warehouse, "active": 1}, [
        "name", "company", "erpnext_warehouse", "default_receiving_bin", "default_shipping_bin", "default_stock_type",
        "inbound_replication", "outbound_replication", "release_replicated_deliveries", "outbound_follow_up",
        "erp_change_policy", "close_short_orders"], as_dict=True)


def _lines_by_wms_warehouse(doc, trigger_only=True):
    """{wms_warehouse_name: (settings, [rows])} for this ERPNext document's item rows."""
    field, value = TRIGGERS[doc.doctype]
    out = {}
    for row in doc.items:
        wh = wms_warehouse_for(row.warehouse)
        if not wh or (trigger_only and wh.get(field) != value): continue
        out.setdefault(wh.name, (wh, []))[1].append(row)
    return out


# ------------------------------------------------------------------ quantities

def _stock_qty(row):
    return flt(row.get("stock_qty")) or flt(row.qty) * flt(row.conversion_factor or 1)


def _done_stock_qty(row, doctype):
    # delivered_qty / received_qty are in the order line's own UOM.
    done = flt(row.delivered_qty) if doctype == "Sales Order" else flt(row.received_qty)
    return done * flt(row.conversion_factor or 1)


def _open_wms_lines(side, order_item):
    """WMS delivery lines for one order line that are still open (not cancelled, not closed)."""
    return frappe.db.sql(f"""
        select i.name, i.parent, i.{side['qty']} qty, i.{side['done']} done, ifnull(i.allocated_quantity, 0) allocated,
               d.docstatus, d.status
        from `tab{side['item']}` i join `tab{side['doctype']}` d on d.name = i.parent
        where i.{side['order_item_field']} = %s and d.docstatus < 2 and ifnull(d.closed_short, 0) = 0
          and d.status not in ('Cancelled', 'Completed')""" if side is OUTBOUND else f"""
        select i.name, i.parent, i.{side['qty']} qty, i.{side['done']} done, 0 allocated, d.docstatus, d.status
        from `tab{side['item']}` i join `tab{side['doctype']}` d on d.name = i.parent
        where i.{side['order_item_field']} = %s and d.docstatus < 2 and ifnull(d.closed_short, 0) = 0
          and d.status not in ('Cancelled', 'Completed')""", order_item, as_dict=True)


def outstanding_for_order_line(order_doctype, row):
    """Stock-UOM quantity of an order line not yet delivered/received and not yet on an open
    WMS delivery - what a new WMS delivery may still take on."""
    side = SIDE[order_doctype]
    on_wms = sum(max(flt(l.qty) - flt(l.done), 0) for l in _open_wms_lines(side, row.name))
    return _stock_qty(row) - _done_stock_qty(row, order_doctype) - on_wms


# ------------------------------------------------------------------ building deliveries

def _delivery_doc(side, wh, header, lines):
    rows = []
    for i, l in enumerate(lines, 1):
        l = dict(l)
        l["line_number"] = i
        rows.append(l)
    doc = frappe.get_doc({"doctype": side["doctype"], "warehouse": wh.name, **header, "items": rows})
    doc.insert(ignore_permissions=True)
    return doc


def _release(doc, wh):
    if not wh.release_replicated_deliveries: return
    doc.flags.ignore_permissions = True
    doc.submit()
    if doc.doctype == "Outbound Delivery" and wh.outbound_follow_up and wh.outbound_follow_up != "None":
        from frappe_wms.services.allocation import allocate_delivery
        from frappe_wms.services.task import create_pick_tasks
        # A follow-up that fails (no stock yet, no bin determination) must not undo the
        # replication itself: the delivery stays released for a planner, with the reason on it.
        try:
            with _savepoint("wms_follow_up"):
                allocate_delivery(doc.name)
                if wh.outbound_follow_up == "Allocate and Create Pick Tasks":
                    create_pick_tasks(doc.name)
        except frappe.ValidationError as e:
            doc.add_comment("Comment", _("Automatic follow-up ({0}) did not run: {1}").format(wh.outbound_follow_up, frappe.utils.strip_html(str(e))))
            frappe.clear_last_message()


class _savepoint:
    def __init__(self, name): self.name = name
    def __enter__(self): frappe.db.savepoint(self.name)
    def __exit__(self, exc_type, exc, tb):
        if exc_type: frappe.db.rollback(save_point=self.name)
        return False


def _inbound_line(row, wh, qty, source_doctype=None, source_name=None):
    po = row.get("purchase_order") if row.doctype == "Purchase Receipt Item" else row.parent
    po_item = row.get("purchase_order_item") if row.doctype == "Purchase Receipt Item" else row.name
    line = {"item": row.item_code, "expected_quantity": qty, "stock_uom": row.stock_uom, "uom": row.uom,
            "conversion_factor": row.conversion_factor, "expected_stock_type": wh.default_stock_type,
            "purchase_order": po, "purchase_order_item": po_item}
    if source_doctype:
        line.update(source_document_type=source_doctype, source_document_number=source_name, source_document_line=row.name)
    return line


def _outbound_line(row, wh, qty, source_doctype=None, source_name=None):
    so = row.get("against_sales_order") if row.doctype == "Delivery Note Item" else row.parent
    so_item = row.get("so_detail") if row.doctype == "Delivery Note Item" else row.name
    line = {"item": row.item_code, "requested_quantity": qty, "stock_uom": row.stock_uom, "uom": row.uom,
            "conversion_factor": row.conversion_factor, "required_stock_type": wh.default_stock_type,
            "sales_order": so, "sales_order_item": so_item}
    if row.get("batch_no"): line["required_batch"] = row.batch_no
    if source_doctype:
        line.update(source_document_type=source_doctype, source_document_number=source_name, source_document_line=row.name)
    return line


def _header(doc, wh):
    number = f"{doc.name}-{wh.name}" if frappe.db.count(SIDE[doc.doctype]["doctype"], {"erp_source_name": doc.name}) == 0 \
        else f"{doc.name}-{wh.name}-{frappe.generate_hash(length=4)}"
    if SIDE[doc.doctype] is INBOUND:
        return {"inbound_delivery_number": number, "supplier": doc.supplier, "company": doc.company, "receiving_bin": wh.default_receiving_bin,
                "posting_date": nowdate(), "expected_arrival": doc.get("schedule_date") or doc.get("posting_date"),
                "external_reference": doc.get("supplier_delivery_note") or doc.name,
                "erp_source_doctype": doc.doctype, "erp_source_name": doc.name}
    return {"outbound_delivery_number": number, "customer": doc.customer, "ship_to_party": doc.get("shipping_address_name"),
            "delivery_date": doc.get("delivery_date") or doc.get("posting_date") or nowdate(), "staging_bin": wh.default_shipping_bin,
            "priority": "Normal", "external_reference": doc.get("po_no") or doc.name,
            "erp_source_doctype": doc.doctype, "erp_source_name": doc.name}


def create_deliveries_for_order(doc, wms_warehouse=None, trigger_only=True, release=True):
    """Creates one WMS delivery per WMS warehouse for the order's outstanding lines. Returns names."""
    side = SIDE[doc.doctype]
    groups = _lines_by_wms_warehouse(doc, trigger_only=trigger_only)
    if wms_warehouse:
        groups = {k: v for k, v in groups.items() if k == wms_warehouse}
        if not groups and not any(wms_warehouse_for(r.warehouse) for r in doc.items):
            # None of the order's warehouses is WMS-managed (or the WMS warehouse has no ERPNext
            # warehouse): the manual button then sends every line to the chosen warehouse.
            wh = frappe._dict(frappe.db.get_value("WMS Warehouse", wms_warehouse, [
                "name", "company", "erpnext_warehouse", "default_receiving_bin", "default_shipping_bin", "default_stock_type",
                "inbound_replication", "outbound_replication", "release_replicated_deliveries", "outbound_follow_up",
                "erp_change_policy", "close_short_orders"], as_dict=True))
            groups = {wh.name: (wh, list(doc.items))}
    created = []
    for wh, rows in groups.values():
        make = _inbound_line if side is INBOUND else _outbound_line
        lines = []
        for row in rows:
            qty = outstanding_for_order_line(doc.doctype, row)
            if qty > EPS: lines.append(make(row, wh, qty))
        if not lines: continue
        delivery = _delivery_doc(side, wh, _header(doc, wh), lines)
        if release: _release(delivery, wh)
        created.append(delivery.name)
    return created


def _create_for_draft_document(doc, wh, rows):
    """Draft Purchase Receipt / Delivery Note -> one WMS delivery mirroring its rows exactly."""
    side = SIDE[doc.doctype]
    make = _inbound_line if side is INBOUND else _outbound_line
    lines = [make(r, wh, _stock_qty(r), doc.doctype, doc.name) for r in rows if _stock_qty(r) > EPS]
    if not lines: return None
    delivery = _delivery_doc(side, wh, _header(doc, wh), lines)
    _release(delivery, wh)
    return delivery.name


# ------------------------------------------------------------------ doc event handlers

def on_order_submit(doc, method=None):
    """Purchase Order / Sales Order submitted -> replicate where the warehouse asks for it."""
    names = create_deliveries_for_order(doc)
    if names:
        frappe.msgprint(_("Sent to the warehouse as {0}").format(", ".join(names)), alert=True, indicator="blue")


def _is_started(delivery):
    side = INBOUND if delivery.doctype == "Inbound Delivery" else OUTBOUND
    fields = [side["done"]] + (["picked_quantity"] if side is OUTBOUND else [])
    rows = frappe.get_all(side["item"], filters={"parent": delivery.name}, fields=fields)
    if any(flt(r.get(side["done"])) > EPS for r in rows): return True
    if side is OUTBOUND:
        if any(flt(r.get("picked_quantity")) > EPS for r in rows): return True
        if frappe.db.exists("Stock Allocation", {"outbound_delivery": delivery.name, "status": ["!=", "Cancelled"]}): return True
    else:
        if frappe.db.exists("Goods Receipt", {"inbound_delivery": delivery.name, "docstatus": 1}): return True
    return False


def _withdraw(delivery_name, doctype, reason):
    """Takes a not-started WMS delivery back: cancelled if released, deleted if still a draft."""
    d = frappe.get_doc(doctype, delivery_name)
    if d.docstatus == 0:
        frappe.delete_doc(doctype, d.name, ignore_permissions=True, force=True)
    elif d.docstatus == 1:
        d.flags.ignore_permissions = True
        d.cancel()
        d.add_comment("Comment", reason)


def _linked_deliveries(doc):
    """Open WMS deliveries carrying lines of this ERPNext document."""
    side = SIDE[doc.doctype]
    if doc.doctype in ("Purchase Receipt", "Delivery Note"):
        names = frappe.get_all(side["doctype"], filters={"erp_source_doctype": doc.doctype, "erp_source_name": doc.name, "docstatus": ["<", 2]}, pluck="name")
    else:
        names = frappe.get_all(side["item"], filters={side["order_field"]: doc.name}, pluck="parent", distinct=True)
        names = [n for n in names if frappe.db.get_value(side["doctype"], n, "docstatus") < 2]
    return [frappe.get_doc(side["doctype"], n) for n in names
            if frappe.db.get_value(side["doctype"], n, "status") not in ("Cancelled", "Completed")
            and not frappe.db.get_value(side["doctype"], n, "closed_short")]


def _refuse_started(delivery, what):
    frappe.throw(_("{0} is already being executed in warehouse {1} ({2}) - {3} is not possible any more. "
                   "Complete it short or reverse the warehouse steps first.").format(
        frappe.bold(delivery.name), delivery.warehouse, delivery.status, what), title=_("In Warehouse Execution"))


def on_order_cancel(doc, method=None):
    """Order / draft document cancelled or deleted: take back its WMS deliveries, or refuse."""
    if frappe.flags.get("wms_posting"): return
    for d in _linked_deliveries(doc):
        if _is_started(d): _refuse_started(d, _("cancelling {0}").format(doc.name))
    for d in _linked_deliveries(doc):
        _withdraw(d.name, d.doctype, _("Withdrawn: {0} {1} was cancelled in ERPNext").format(doc.doctype, doc.name))


def on_order_update_after_submit(doc, method=None):
    """"Update Items" on a submitted order: follow quantity changes into its WMS deliveries."""
    if frappe.flags.get("wms_posting"): return
    side = SIDE[doc.doctype]
    groups = _lines_by_wms_warehouse(doc, trigger_only=False)
    for wh, rows in groups.values():
        policy = wh.erp_change_policy or "Adapt Until Execution Starts"
        for row in rows:
            open_lines = _open_wms_lines(side, row.name)
            if not open_lines: continue
            wanted = _stock_qty(row) - _done_stock_qty(row, doc.doctype)
            on_wms = sum(max(flt(l.qty) - flt(l.done), 0) for l in open_lines)
            diff = wanted - on_wms
            if abs(diff) <= EPS: continue
            if policy == "Block Changes After Replication":
                frappe.throw(_("Row {0} ({1}) is already sent to warehouse {2} and this warehouse does not accept order changes after replication.")
                             .format(row.idx, row.item_code, wh.name), title=_("Order Change Refused"))
            if diff < 0:
                _reduce(side, open_lines, -diff, row, doc)
            else:
                # More ordered: grow the line on a delivery that has not started, else the
                # replication below opens a new delivery for the extra quantity.
                for l in open_lines:
                    d = frappe.get_doc(side["doctype"], l.parent)
                    if not _is_started(d):
                        frappe.db.set_value(side["item"], l.name, side["qty"], flt(l.qty) + diff)
                        d.add_comment("Comment", _("{0} {1} row {2} increased to {3} in ERPNext").format(doc.doctype, doc.name, row.idx, flt(l.qty) + diff))
                        break
        # Increases and brand-new lines: only when the warehouse replicates on order submit.
        field, value = TRIGGERS[doc.doctype]
        if wh.get(field) == value:
            create_deliveries_for_order(doc, wms_warehouse=wh.name)
    # Lines removed from the order entirely.
    remaining = {r.name for r in doc.items}
    for d in _linked_deliveries(doc):
        gone = [i for i in d.items if i.get(side["order_item_field"]) and i.get(side["order_field"]) == doc.name
                and i.get(side["order_item_field"]) not in remaining]
        if gone:
            if _is_started(d): _refuse_started(d, _("removing lines from {0}").format(doc.name))
            _rebuild_without(d, {g.name for g in gone}, doc)


def _reduce(side, open_lines, qty, row, doc):
    """Takes `qty` off the open WMS lines of one order line, newest delivery first."""
    for l in sorted(open_lines, key=lambda x: x.parent, reverse=True):
        if qty <= EPS: break
        d = frappe.get_doc(side["doctype"], l.parent)
        free = flt(l.qty) - flt(l.done) - flt(l.allocated)
        if _is_started(d) and free <= EPS:
            continue
        take = min(qty, max(free, 0))
        if take <= EPS: continue
        new_qty = flt(l.qty) - take
        if new_qty <= EPS and not _is_started(d):
            _rebuild_without(d, {l.name}, doc)
        else:
            frappe.db.set_value(side["item"], l.name, side["qty"], new_qty)
            d.add_comment("Comment", _("{0} {1} row {2} reduced to {3} in ERPNext").format(doc.doctype, doc.name, row.idx, new_qty))
        qty -= take
    if qty > EPS:
        frappe.throw(_("Row {0} ({1}): {2} of the quantity is already allocated, picked or received in the warehouse and cannot be reduced any more.")
                     .format(row.idx, row.item_code, flt(qty)), title=_("In Warehouse Execution"))


def _rebuild_without(delivery, line_names, doc):
    """A not-started delivery losing lines: withdrawn, and re-created with what is left."""
    keep = [i for i in delivery.items if i.name not in line_names]
    wh = frappe._dict(frappe.db.get_value("WMS Warehouse", delivery.warehouse, ["name", "release_replicated_deliveries", "outbound_follow_up"], as_dict=True))
    header = {k: delivery.get(k) for k in delivery.as_dict() if k not in ("name", "items", "docstatus", "status", "amended_from", "creation", "modified",
              "modified_by", "owner", "idx", "doctype", "allocation_status", "picking_status", "packing_status", "loading_status",
              "goods_issue_status", "receipt_status", "process_status", "_user_tags", "_comments", "_assign", "_liked_by", "_seen")}
    lines = [{k: i.get(k) for k in i.as_dict() if k not in ("name", "parent", "parentfield", "parenttype", "idx", "doctype", "creation", "modified",
              "modified_by", "owner", "docstatus", "line_number")} for i in keep]
    _withdraw(delivery.name, delivery.doctype, _("Replaced after {0} {1} changed in ERPNext").format(doc.doctype, doc.name))
    if lines:
        header["outbound_delivery_number" if delivery.doctype == "Outbound Delivery" else "inbound_delivery_number"] = f"{header.get('outbound_delivery_number') or header.get('inbound_delivery_number')}-{frappe.generate_hash(length=3)}"
        new = _delivery_doc(INBOUND if delivery.doctype == "Inbound Delivery" else OUTBOUND, wh, header, lines)
        _release(new, wh)
        return new.name


# ------------------------------------------------------------------ draft PR / DN (ASN, ECC delivery)

def draft_document_is_replicated(doc):
    """True when every WMS-managed row of this draft PR/DN belongs to a warehouse that uses the
    draft as its delivery document - the stock guard then lets the draft exist."""
    field, value = TRIGGERS[doc.doctype]
    managed = [wms_warehouse_for(r.warehouse) for r in doc.items if wms_warehouse_for(r.warehouse)]
    return bool(managed) and all(w.get(field) == value for w in managed)


def on_draft_document_update(doc, method=None):
    """Draft Purchase Receipt / Delivery Note saved: create or follow its WMS delivery."""
    if doc.docstatus != 0 or frappe.flags.get("wms_posting") or doc.get("is_return"): return
    link_field = "wms_outbound_delivery" if doc.doctype == "Delivery Note" else "wms_inbound_delivery"
    groups = _lines_by_wms_warehouse(doc)
    if not groups: return
    if len(groups) > 1:
        frappe.throw(_("A {0} replicated to the warehouse must use one WMS warehouse; split it per warehouse.").format(doc.doctype))
    wh, rows = next(iter(groups.values()))
    existing = doc.get(link_field)
    if existing and frappe.db.exists(SIDE[doc.doctype]["doctype"], existing):
        d = frappe.get_doc(SIDE[doc.doctype]["doctype"], existing)
        if d.docstatus < 2 and not _same_lines(d, rows):
            if _is_started(d): _refuse_started(d, _("changing {0}").format(doc.name))
            _withdraw(d.name, d.doctype, _("Replaced after {0} {1} changed").format(doc.doctype, doc.name))
            existing = None
        elif d.docstatus < 2:
            return
    name = _create_for_draft_document(doc, wh, rows)
    frappe.db.set_value(doc.doctype, doc.name, link_field, name, update_modified=False)
    doc.set(link_field, name)


def _same_lines(delivery, rows):
    side = INBOUND if delivery.doctype == "Inbound Delivery" else OUTBOUND
    mine = sorted((i.item, round(flt(i.get(side["qty"])), 6), i.source_document_line) for i in delivery.items)
    theirs = sorted((r.item_code, round(_stock_qty(r), 6), r.name) for r in rows if _stock_qty(r) > EPS)
    return mine == theirs


def before_draft_document_submit(doc, method=None):
    """A replicated draft PR / DN is posted by the warehouse (goods receipt / goods issue), never by hand."""
    if frappe.flags.get("wms_posting") or doc.flags.get("wms_managed_posting"): return
    link = doc.get("wms_outbound_delivery" if doc.doctype == "Delivery Note" else "wms_inbound_delivery")
    if link:
        frappe.throw(_("{0} is being executed by the warehouse as {1}; it is submitted automatically by the {2}.").format(
            doc.name, link, _("Goods Issue") if doc.doctype == "Delivery Note" else _("Goods Receipt")), title=_("Warehouse-Managed Document"))


# ------------------------------------------------------------------ completion & report back

def complete_short(doctype, delivery_name, reason=None):
    """Closes a WMS delivery at what was actually received/issued (SAP: adjust delivery quantity
    / complete delivery). Open, unstarted quantity is removed; ERPNext is told per configuration."""
    from frappe_wms.utils import require_role
    require_role("WMS Supervisor", "WMS Administrator")
    side = INBOUND if doctype == "Inbound Delivery" else OUTBOUND
    d = frappe.get_doc(doctype, delivery_name, for_update=True)
    if d.docstatus != 1: frappe.throw(_("Only a released (submitted) delivery can be completed short"))
    if d.closed_short or d.status in ("Completed", "Cancelled"): frappe.throw(_("{0} is already closed").format(d.name))
    if side is OUTBOUND:
        from frappe_wms.services.allocation import cancel_allocations_for_delivery
        for i in d.items:
            if flt(i.picked_quantity) - flt(i.issued_quantity) > EPS:
                frappe.throw(_("Line {0} ({1}): {2} is picked but not issued yet. Ship or reverse it before completing short.")
                             .format(i.line_number, i.item, flt(i.picked_quantity) - flt(i.issued_quantity)))
        cancel_allocations_for_delivery(d.name)
        from frappe_wms.services.cross_dock import redirect_cross_dock_to_putaway
        redirect_cross_dock_to_putaway(d.name)
    else:
        if frappe.db.exists("Goods Receipt", {"inbound_delivery": d.name, "docstatus": 0}):
            frappe.throw(_("A draft Goods Receipt exists for {0}; post or delete it first").format(d.name))
    for i in d.items:
        frappe.db.set_value(side["item"], i.name, side["qty"], flt(i.get(side["done"])))
    d.db_set({"status": "Completed", "closed_short": 1, "close_reason": reason or _("Completed short")}, update_modified=True)
    d.add_comment("Comment", _("Completed short by {0}: {1}").format(frappe.session.user, reason or "-"))
    report_completion(d)
    return {"delivery": d.name, "status": "Completed"}


def report_completion(delivery):
    """A WMS delivery closed: withdraw an empty source draft, and close the ERPNext order
    remainder when the warehouse is configured to."""
    side = INBOUND if delivery.doctype == "Inbound Delivery" else OUTBOUND
    done = sum(flt(i.get(side["done"])) for i in delivery.items)
    if delivery.erp_source_doctype in ("Purchase Receipt", "Delivery Note") and delivery.erp_source_name:
        src = frappe.db.get_value(delivery.erp_source_doctype, delivery.erp_source_name, "docstatus")
        if src == 0 and done <= EPS:
            previous, frappe.flags.wms_posting = frappe.flags.get("wms_posting"), True
            try:
                frappe.delete_doc(delivery.erp_source_doctype, delivery.erp_source_name, ignore_permissions=True, force=True)
            finally:
                frappe.flags.wms_posting = previous
    if not frappe.db.get_value("WMS Warehouse", delivery.warehouse, "close_short_orders"): return
    order_doctype = "Purchase Order" if side is INBOUND else "Sales Order"
    for order in {i.get(side["order_field"]) for i in delivery.items if i.get(side["order_field"])}:
        # Another open WMS delivery still working on this order keeps it open.
        still_open = frappe.db.sql(f"""select 1 from `tab{side['item']}` i join `tab{side['doctype']}` d on d.name=i.parent
            where i.{side['order_field']}=%s and d.docstatus=1 and d.name<>%s and ifnull(d.closed_short,0)=0
              and d.status not in ('Cancelled','Completed') limit 1""", (order, delivery.name))
        if still_open: continue
        o = frappe.get_doc(order_doctype, order)
        if o.docstatus == 1 and o.status not in ("Closed", "Completed", "Cancelled"):
            o.flags.ignore_permissions = True
            o.update_status("Closed")
            o.add_comment("Comment", _("Closed by the warehouse: {0} was completed short").format(delivery.name))


# ------------------------------------------------------------------ status for ERPNext forms

def wms_status(doctype, name):
    side = SIDE.get(doctype)
    if not side: return []
    if doctype in ("Purchase Receipt", "Delivery Note"):
        names = frappe.get_all(side["doctype"], filters={"erp_source_doctype": doctype, "erp_source_name": name}, pluck="name")
    else:
        names = frappe.get_all(side["item"], filters={side["order_field"]: name}, pluck="parent", distinct=True)
    fields = ["name", "warehouse", "status", "docstatus", "closed_short"] + (
        ["receipt_status", "process_status"] if side is INBOUND else ["allocation_status", "picking_status", "packing_status", "loading_status", "goods_issue_status"])
    return frappe.get_all(side["doctype"], filters={"name": ["in", names or [""]]}, fields=fields, order_by="creation asc")
