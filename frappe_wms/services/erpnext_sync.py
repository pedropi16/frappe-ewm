from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import flt

@contextmanager
def _as_system_user():
    # ERPNext's make_purchase_receipt/make_delivery_note/make_sales_return/make_purchase_return
    # mappers each call frappe.model.mapper.get_mapped_doc without passing ignore_permissions,
    # which makes get_mapped_doc run target_doc.check_permission("create") against the CURRENT
    # session user before we ever get the resulting doc back - setting .flags.ignore_permissions
    # on it afterwards (already done below, for insert/submit) is too late to prevent that check.
    # Reproduced live in production: a WMS Receiver role posting a real Goods Receipt got a
    # PermissionError for "Purchase Receipt", a native ERPNext doctype no warehouse-floor role
    # should need direct create/submit rights on - this mirror document is this call's own
    # internal accounting side effect, not something the RF operator is creating themselves.
    # Runs just the mapper call as Administrator and restores the real user immediately after.
    #
    # frappe.set_user() is designed for background/script contexts, not for impersonating
    # mid-request and switching back - besides session.user, it also overwrites session.sid with
    # the plain username string and wipes session.data (which holds, among other things, the
    # CSRF token) to an empty dict. Request teardown writes session.sid straight into the
    # response's `sid` cookie AND persists session.data as-is into the Sessions table row for
    # that sid (frappe/sessions.py Session.update) - restoring only .user/.sid after calling
    # set_user() a second time still leaves that row's stored data blanked out, which reads back
    # as a broken session on the very next request. Reproduced live via the e2e suite: a second
    # "Post receipt" click right after a successful one bounced straight to /login. Swapping the
    # whole session object back (not just individual fields) is what actually undoes every part
    # of what set_user() touched. Restoring by mutating the SAME dict object back to its original
    # key/value snapshot (rather than pointing frappe.local.session at a new object) matters too:
    # frappe.sessions.Session keeps its own reference to this exact object (self.data), read
    # directly at request teardown - replacing frappe.local.session with a different object would
    # leave that reference still holding the corrupted one.
    current_session = dict(frappe.local.session)
    frappe.set_user("Administrator")
    try:
        yield
    finally:
        frappe.local.session.update(current_session)
        frappe.local.role_permissions = {}
        frappe.local.user_perms = None

def _insert_and_submit_as_system(doc):
    # Same reasoning as _as_system_user's own docstring, but for the plain Stock Entry mirrors
    # (built directly via frappe.new_doc, not one of ERPNext's get_mapped_doc-based mappers):
    # insert(ignore_permissions=True) only covers checks on THIS doc. Its own on_submit -
    # specifically make_bundle_using_old_serial_batch_fields, for any batch/serial-tracked row -
    # creates a separate Serial and Batch Bundle document with no ignore_permissions of its own,
    # checked against frappe.session.user. Reproduced live: a WMS-role floor user posting a
    # perfectly ordinary batch-controlled Move/Count/etc. got a PermissionError for "Serial and
    # Batch Bundle" - another native ERPNext doctype no warehouse-floor role should need direct
    # access to just to post a WMS movement that happens to touch a batch/serial item.
    with _as_system_user():
        doc.insert(ignore_permissions=True)
        doc.submit()

def _erpnext_warehouse(wms_warehouse):
    return frappe.db.get_value("WMS Warehouse", wms_warehouse, "erpnext_warehouse")

def _make_stock_entry(*, stock_entry_type, company, remarks):
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = stock_entry_type
    se.company = company
    se.remarks = remarks
    return se

def _resolve_rate(item_code):
    rate = flt(frappe.db.get_value("Item", item_code, "valuation_rate"))
    if not rate:
        rate = flt(frappe.db.get_value("Item", item_code, "last_purchase_rate"))
    return rate

def _append_row(se, row, *, target_field, erpnext_warehouse, uom=None, conversion_factor=None):
    # row.quantity is always in stock_uom (the WMS-internal invariant - ledger/balance math
    # never changes). ERPNext's Stock Entry Detail "qty" is the *transactional-UOM* quantity,
    # with stock_qty = qty * conversion_factor computed by ERPNext itself, so a non-1
    # conversion_factor here has to divide row.quantity back down - otherwise ERPNext would
    # double-apply the conversion when it derives stock_qty. This only relabels the mirrored
    # row into the transactional unit for display; the actual stock movement is unchanged.
    conversion_factor = flt(conversion_factor) or 1.0
    uom = uom or row.stock_uom
    values = {
        "item_code": row.item,
        "qty": flt(row.quantity) / conversion_factor,
        "uom": uom,
        "stock_uom": row.stock_uom,
        "conversion_factor": conversion_factor,
        "batch_no": row.batch_no,
        "serial_no": row.serial_no,
        "use_serial_batch_fields": 1,
        target_field: erpnext_warehouse,
        # Stock Entry Detail's WMS Stock Type Inventory Dimension fields mirror its
        # s_warehouse/t_warehouse pair: unprefixed = source, "to_" = target.
        "to_wms_stock_type" if target_field == "t_warehouse" else "wms_stock_type": row.stock_type,
    }
    if target_field == "t_warehouse":
        # A Material Issue consumes valuation layers ERPNext already has for this stock, so it
        # never needs allow_zero_valuation_rate - that flag exists only for an incoming leg with
        # no cost basis, so only the receipt direction resolves (or falls back to) one.
        rate = _resolve_rate(row.item)
        if rate:
            values["basic_rate"] = rate
        else:
            values["allow_zero_valuation_rate"] = 1
    se.append("items", values)

def _cancel_doc(doctype, name):
    if not name: return
    doc = frappe.get_doc(doctype, name)
    if doc.docstatus == 1:
        doc.flags.ignore_permissions = True
        doc.cancel()

# --- Goods Receipt -> Stock Entry (no PO) or Purchase Receipt (PO-linked) ---

def _po_link_for_gr_row(row):
    if not row.inbound_delivery_item: return None, None
    return frappe.db.get_value("Inbound Delivery Item", row.inbound_delivery_item, ["purchase_order", "purchase_order_item"]) or (None, None)

def _work_order_link_for_gr_row(row):
    if not row.inbound_delivery_item: return None
    source_type, source_number = frappe.db.get_value("Inbound Delivery Item", row.inbound_delivery_item, ["source_document_type", "source_document_number"]) or (None, None)
    return source_number if source_type == "Work Order" else None

def _return_link_for_gr_row(row):
    if not row.inbound_delivery_item: return None, None
    source_type, source_number, source_line = frappe.db.get_value(
        "Inbound Delivery Item", row.inbound_delivery_item, ["source_document_type", "source_document_number", "source_document_line"]
    ) or (None, None, None)
    return (source_number, source_line) if source_type == "Delivery Note" else (None, None)

def sync_goods_receipt(doc):
    if doc.get("erpnext_stock_entry") or doc.get("erpnext_purchase_receipt") or doc.get("erpnext_delivery_note"): return
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return
    po_links = [_po_link_for_gr_row(row) for row in doc.items]
    linked = [l for l in po_links if l[0]]
    if linked and len(linked) != len(doc.items):
        frappe.throw(_("Goods Receipt {0} mixes Purchase-Order-linked and standalone lines; post them as separate receipts").format(doc.name))
    if linked:
        _sync_goods_receipt_to_purchase_receipt(doc, erpnext_warehouse, po_links)
        return
    # Customer return: a receipt whose Inbound Delivery Item rows were all created from
    # create_return_inbound_delivery (source_document_type "Delivery Note") mirrors to a proper
    # Sales Return (a credit Delivery Note, is_return=1) against the original, instead of an
    # unlinked standalone receipt that would double-count the customer's stock as newly bought in.
    return_links = [_return_link_for_gr_row(row) for row in doc.items]
    linked_returns = [l for l in return_links if l[0]]
    if linked_returns and len(linked_returns) != len(doc.items):
        frappe.throw(_("Goods Receipt {0} mixes return and standalone lines; post them as separate receipts").format(doc.name))
    if linked_returns:
        if len({l[0] for l in linked_returns}) != 1:
            frappe.throw(_("Goods Receipt {0} references more than one Delivery Note; post them separately").format(doc.name))
        _sync_goods_receipt_to_return_delivery_note(doc, erpnext_warehouse, return_links)
        return
    # Production supply, FG receipt: a receipt whose Inbound Delivery Item rows were all
    # created from create_fg_receipt_from_work_order (source_document_type "Work Order")
    # mirrors to a Stock Entry carrying that Work Order's link, instead of an unlinked
    # standalone receipt (see _sync_goods_receipt_to_manufacture_stock_entry for why it's
    # still a Material Receipt rather than a "Manufacture" purpose entry).
    work_orders = [_work_order_link_for_gr_row(row) for row in doc.items]
    linked_wo = [w for w in work_orders if w]
    if linked_wo and len(linked_wo) != len(doc.items):
        frappe.throw(_("Goods Receipt {0} mixes Work-Order-sourced and standalone lines; post them as separate receipts").format(doc.name))
    if linked_wo:
        if len(set(linked_wo)) != 1:
            frappe.throw(_("Goods Receipt {0} references more than one Work Order; post them separately").format(doc.name))
        _sync_goods_receipt_to_manufacture_stock_entry(doc, erpnext_warehouse, linked_wo[0])
    else:
        _sync_goods_receipt_to_stock_entry(doc, erpnext_warehouse)

def _sync_goods_receipt_to_manufacture_stock_entry(doc, erpnext_warehouse, work_order_name):
    # ERPNext's "Manufacture" purpose is backflush-oriented - it hard-requires at least one
    # raw-material consumption row alongside the FG row (validate_raw_materials_exists()),
    # which this receipt-only flow doesn't have (backflushing is explicitly out of scope here,
    # see frappe_wms.events.work_order). "Material Receipt" is used instead, keeping the
    # work_order link for traceability without claiming a backflush that didn't happen.
    # ERPNext clears/ignores the work_order field on a non-manufacturing purpose (confirmed
    # empirically: it's silently dropped on save for "Material Receipt"), so the Work Order
    # reference lives in remarks here instead - the WMS-side link (Goods Receipt.
    # erpnext_stock_entry, Inbound Delivery Item.source_document_number) is authoritative.
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Receipt", company=company, remarks=f"frappe_wms Goods Receipt {doc.name} (Work Order {work_order_name})")
    for row in doc.items:
        _append_row(se, row, target_field="t_warehouse", erpnext_warehouse=erpnext_warehouse)
    se.flags.wms_managed_posting = True
    _insert_and_submit_as_system(se)
    doc.db_set("erpnext_stock_entry", se.name, update_modified=False)

def _sync_goods_receipt_to_stock_entry(doc, erpnext_warehouse):
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Receipt", company=company, remarks=f"frappe_wms Goods Receipt {doc.name}")
    for row in doc.items:
        uom, conversion_factor = (None, None)
        if row.inbound_delivery_item:
            uom, conversion_factor = frappe.db.get_value("Inbound Delivery Item", row.inbound_delivery_item, ["uom", "conversion_factor"]) or (None, None)
        _append_row(se, row, target_field="t_warehouse", erpnext_warehouse=erpnext_warehouse, uom=uom, conversion_factor=conversion_factor)
    se.flags.wms_managed_posting = True
    _insert_and_submit_as_system(se)
    doc.db_set("erpnext_stock_entry", se.name, update_modified=False)

_PR_TEMPLATE_FIELDS = ("item_code", "item_name", "description", "uom", "conversion_factor", "rate",
    "purchase_order", "purchase_order_item", "expense_account", "cost_center", "asset_location", "asset_category")

def _sync_goods_receipt_to_purchase_receipt(doc, erpnext_warehouse, po_links):
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    po_names = {link[0] for link in po_links}
    if len(po_names) != 1:
        frappe.throw(_("Goods Receipt {0} references more than one Purchase Order; post them separately").format(doc.name))
    # One Purchase Receipt row per (PO item, batch, serial) group, not one summed row per PO
    # item - a summed row never carried batch_no/serial_no at all (they were simply never
    # copied onto it), so ERPNext's own valuation/traceability silently lost which batch or
    # serial the receipt actually recorded, and submission itself would fail outright for a
    # batch/serial-managed item (ERPNext requires that data on such a row).
    groups = {}
    for row, (_po, po_item) in zip(doc.items, po_links):
        key = (po_item, row.batch_no, row.serial_no, row.stock_type)
        groups[key] = groups.get(key, 0) + flt(row.quantity)

    # Everything from here on - the mapping, row assembly, insert and submit - runs as the
    # system user. It's not just the initial create-permission check that needs this: ERPNext's
    # own Purchase Receipt controller does further permission-gated lookups of its own deep in
    # validate() (e.g. get_item_details() re-reading the Item doctype), so patching only the
    # first check left this still failing for a real floor role at insert() time.
    with _as_system_user():
        pr = make_purchase_receipt(po_names.pop())
        template_by_po_item = {item.purchase_order_item: item for item in pr.items}
        kept = []
        for (po_item, batch_no, serial_no, stock_type), qty in groups.items():
            template = template_by_po_item.get(po_item)
            if not template: continue
            row = pr.append("items", {})
            for field in _PR_TEMPLATE_FIELDS: row.set(field, template.get(field))
            # qty here is already a stock-uom-derived sum (WMS row.quantity is always stock_uom).
            # Setting row.qty = qty directly while leaving the PO row's own conversion_factor in
            # place would make ERPNext double-apply it when deriving stock_qty - divide back down
            # so stock_qty (the actual stock movement) recomputes to exactly qty.
            row.qty = qty / flt(template.conversion_factor or 1)
            row.stock_qty = qty
            row.warehouse = erpnext_warehouse
            row.batch_no = batch_no
            row.serial_no = serial_no
            row.use_serial_batch_fields = 1
            # Purchase Receipt Item's dimension field for its primary "warehouse" (the
            # receiving/target warehouse) is unprefixed, unlike Stock Entry's source/target split.
            row.wms_stock_type = stock_type
            kept.append(row)
        if not kept: frappe.throw(_("No matching Purchase Order rows found for Goods Receipt {0}").format(doc.name))
        # Drop whatever template rows make_purchase_receipt pre-filled from the PO itself (their
        # qty reflects what was ordered, not what this specific receipt actually recorded) and keep
        # only the grouped rows just built above.
        pr.items = kept
        pr.flags.ignore_permissions = True
        pr.flags.wms_managed_posting = True
        pr.insert(ignore_permissions=True)
        pr.submit()
    doc.db_set("erpnext_purchase_receipt", pr.name, update_modified=False)

_RETURN_DN_TEMPLATE_FIELDS = ("item_code", "item_name", "description", "uom", "conversion_factor", "rate", "dn_detail")

def _sync_goods_receipt_to_return_delivery_note(doc, erpnext_warehouse, return_links):
    # Customer return: erpnext.stock.doctype.delivery_note.delivery_note.make_sales_return
    # builds a correctly-configured return document (is_return=1, return_against, negative
    # qty convention, party/accounts from the original) - reused here purely as a template,
    # the same way the Purchase Order/Sales Order paths above reuse make_purchase_receipt/
    # make_delivery_note, then rebuilt row-for-row from what this receipt actually recorded
    # (which may be a partial or split-by-batch subset of the original delivery, not
    # necessarily the whole thing back at once).
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return
    dn_name = return_links[0][0]
    groups = {}
    for row, (_dn, dn_item) in zip(doc.items, return_links):
        key = (dn_item, row.batch_no, row.serial_no, row.stock_type)
        groups[key] = groups.get(key, 0) + flt(row.quantity)

    # See the comment on the Purchase Receipt mirror above - the whole mapping/insert/submit
    # sequence needs to run as the system user, not just the initial mapper call.
    with _as_system_user():
        ret = make_sales_return(dn_name)
        template_by_dn_item = {item.dn_detail: item for item in ret.items}
        kept = []
        for (dn_item, batch_no, serial_no, stock_type), qty in groups.items():
            template = template_by_dn_item.get(dn_item)
            if not template: continue
            row = ret.append("items", {})
            for field in _RETURN_DN_TEMPLATE_FIELDS: row.set(field, template.get(field))
            # ERPNext's own return convention: qty/stock_qty are negative (this reverses the
            # original outgoing movement). qty here is already stock-uom, so divide by the
            # original row's own conversion_factor before assigning the transactional qty, same
            # double-application guard as every other mirror above.
            row.qty = -(qty / flt(template.conversion_factor or 1))
            row.stock_qty = -qty
            row.warehouse = erpnext_warehouse
            row.batch_no = batch_no
            row.serial_no = serial_no
            row.use_serial_batch_fields = 1
            row.wms_stock_type = stock_type
            kept.append(row)
        if not kept: frappe.throw(_("No matching Delivery Note rows found for Goods Receipt {0}").format(doc.name))
        ret.items = kept
        ret.flags.ignore_permissions = True
        ret.flags.wms_managed_posting = True
        ret.insert(ignore_permissions=True)
        ret.submit()
    doc.db_set("erpnext_delivery_note", ret.name, update_modified=False)

# --- Warehouse Request (Work Order material staging) -> Stock Entry ---

def sync_work_order_material_transfer(request):
    # Fires once a Work-Order-referencing staging Warehouse Request (frappe_wms.events.
    # work_order.on_submit) reaches Completed - mirrors the physical staging move as an
    # ERPNext Material Transfer for Manufacture against the same Work Order, the same way
    # every other WMS transaction mirrors to its ERPNext equivalent.
    if request.get("reference_doctype") != "Work Order" or request.get("erpnext_stock_entry"): return None
    erpnext_warehouse = _erpnext_warehouse(request.warehouse)
    if not erpnext_warehouse: return None
    wo = frappe.db.get_value("Work Order", request.reference_name, ["wip_warehouse", "company"], as_dict=True)
    if not wo or not wo.wip_warehouse: return None
    se = _make_stock_entry(stock_entry_type="Material Transfer for Manufacture", company=wo.company, remarks=f"frappe_wms Warehouse Request {request.name}")
    se.work_order = request.reference_name
    # Both legs (s_warehouse and t_warehouse) sit on this one row - unlike a Goods
    # Receipt/Issue's separate incoming/outgoing rows, ERPNext derives this transfer's
    # valuation from the source warehouse's existing stock automatically, so no manual
    # basic_rate/allow_zero_valuation_rate override is needed here.
    se.append("items", {
        "item_code": request.product, "qty": flt(request.confirmed_quantity), "uom": request.stock_uom, "stock_uom": request.stock_uom,
        "conversion_factor": 1, "s_warehouse": erpnext_warehouse, "t_warehouse": wo.wip_warehouse,
        "wms_stock_type": request.stock_type, "to_wms_stock_type": request.stock_type, "use_serial_batch_fields": 1,
    })
    se.flags.wms_managed_posting = True
    _insert_and_submit_as_system(se)
    frappe.db.set_value("Warehouse Request", request.name, "erpnext_stock_entry", se.name)
    return se.name

# --- WMS Quality Inspection -> Stock Entry (same-warehouse stock-type change) ---
#
# A QI decision moves stock between WMS Stock Types (QUALITY -> AVAILABLE/BLOCKED) without
# moving the physical warehouse at all - this was always the intended design (see the target
# ERPNext-integration table in app_gap.md: "Material Transfer inside the same Warehouse that
# changes the stock-type Inventory Dimension"), but nothing ever called it, so ERPNext's own
# stock ledger and reports kept showing released stock as still sitting in QUALITY forever.
# Confirmed live that ERPNext accepts a Material Transfer row whose s_warehouse and
# t_warehouse are the same physical warehouse (only the Inventory Dimension differs).

def sync_quality_inspection(doc, passed, failed):
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return None
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Transfer", company=company, remarks=f"frappe_wms Quality Inspection {doc.name}")
    for qty, to_stock_type in ((passed, doc.passed_to_stock_type), (failed, doc.failed_to_stock_type)):
        if qty <= 0: continue
        se.append("items", {
            "item_code": doc.product, "qty": flt(qty), "uom": doc.stock_uom, "stock_uom": doc.stock_uom,
            "conversion_factor": 1, "batch_no": doc.batch_no, "serial_no": doc.serial_no, "use_serial_batch_fields": 1,
            "s_warehouse": erpnext_warehouse, "t_warehouse": erpnext_warehouse,
            "wms_stock_type": doc.from_stock_type, "to_wms_stock_type": to_stock_type,
            # Normally derived from the source warehouse's own existing valuation (a same-
            # warehouse transfer creates no new value) - but that only works once ERPNext has
            # ever actually valued this item in this warehouse. A product whose stock has only
            # ever moved through WMS's own ledger (never yet mirrored through a valued GR/PR)
            # has nothing for ERPNext to derive a rate from at all; this is the same fallback
            # _append_row already uses for exactly that gap on the receiving side.
            "allow_zero_valuation_rate": 1,
        })
    if not se.items: return None
    se.flags.wms_managed_posting = True
    _insert_and_submit_as_system(se)
    return se.name

# --- WMS Posting Change -> Stock Entry (same-warehouse stock-type change, generic) ---
#
# Same mechanism as sync_quality_inspection above, generalized: any stock-type-only change
# (not just a QI pass/fail decision) needs the same same-warehouse Material Transfer so
# ERPNext's own stock ledger and reports don't keep showing stock under its old status forever.

def sync_posting_change(doc):
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return None
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Transfer", company=company, remarks=f"frappe_wms Posting Change {doc.name}")
    se.append("items", {
        "item_code": doc.product, "qty": flt(doc.quantity), "uom": doc.stock_uom, "stock_uom": doc.stock_uom,
        "conversion_factor": 1, "batch_no": doc.batch_no, "serial_no": doc.serial_no, "use_serial_batch_fields": 1,
        "s_warehouse": erpnext_warehouse, "t_warehouse": erpnext_warehouse,
        "wms_stock_type": doc.from_stock_type, "to_wms_stock_type": doc.to_stock_type,
        "allow_zero_valuation_rate": 1,  # see sync_quality_inspection above for why
    })
    se.flags.wms_managed_posting = True
    _insert_and_submit_as_system(se)
    return se.name

def reverse_goods_receipt(doc):
    if doc.get("erpnext_purchase_receipt"):
        _cancel_doc("Purchase Receipt", doc.erpnext_purchase_receipt)
    elif doc.get("erpnext_delivery_note"):
        _cancel_doc("Delivery Note", doc.erpnext_delivery_note)
    else:
        _cancel_doc("Stock Entry", doc.get("erpnext_stock_entry"))

# --- Goods Issue -> Stock Entry (no SO) or Delivery Note (SO-linked) ---

def _so_link_for_gi_row(row):
    if not row.outbound_delivery_item: return None, None
    return frappe.db.get_value("Outbound Delivery Item", row.outbound_delivery_item, ["sales_order", "sales_order_item"]) or (None, None)

def _return_link_for_gi_row(row):
    if not row.outbound_delivery_item: return None, None
    source_type, source_number, source_line = frappe.db.get_value(
        "Outbound Delivery Item", row.outbound_delivery_item, ["source_document_type", "source_document_number", "source_document_line"]
    ) or (None, None, None)
    return (source_number, source_line) if source_type == "Purchase Receipt" else (None, None)

def sync_goods_issue(doc):
    if doc.get("erpnext_stock_entry") or doc.get("erpnext_delivery_note") or doc.get("erpnext_purchase_receipt"): return
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return
    so_links = [_so_link_for_gi_row(row) for row in doc.items]
    linked = [l for l in so_links if l[0]]
    if linked and len(linked) != len(doc.items):
        frappe.throw(_("Goods Issue {0} mixes Sales-Order-linked and standalone lines; post them as separate issues").format(doc.name))
    if linked:
        _sync_goods_issue_to_delivery_note(doc, erpnext_warehouse, so_links)
        return
    # Return to vendor: a Goods Issue whose Outbound Delivery Item rows were all created from
    # create_return_outbound_delivery (source_document_type "Purchase Receipt") mirrors to a
    # proper return Purchase Receipt (is_return=1) against the original, instead of an unlinked
    # standalone issue that would just look like ordinary consumption.
    return_links = [_return_link_for_gi_row(row) for row in doc.items]
    linked_returns = [l for l in return_links if l[0]]
    if linked_returns and len(linked_returns) != len(doc.items):
        frappe.throw(_("Goods Issue {0} mixes return and standalone lines; post them as separate issues").format(doc.name))
    if linked_returns:
        if len({l[0] for l in linked_returns}) != 1:
            frappe.throw(_("Goods Issue {0} references more than one Purchase Receipt; post them separately").format(doc.name))
        _sync_goods_issue_to_return_purchase_receipt(doc, erpnext_warehouse, return_links)
        return
    _sync_goods_issue_to_stock_entry(doc, erpnext_warehouse)

def _sync_goods_issue_to_stock_entry(doc, erpnext_warehouse):
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Issue", company=company, remarks=f"frappe_wms Goods Issue {doc.name}")
    for row in doc.items:
        uom, conversion_factor = (None, None)
        if row.outbound_delivery_item:
            uom, conversion_factor = frappe.db.get_value("Outbound Delivery Item", row.outbound_delivery_item, ["uom", "conversion_factor"]) or (None, None)
        _append_row(se, row, target_field="s_warehouse", erpnext_warehouse=erpnext_warehouse, uom=uom, conversion_factor=conversion_factor)
    se.flags.wms_managed_posting = True
    _insert_and_submit_as_system(se)
    doc.db_set("erpnext_stock_entry", se.name, update_modified=False)

_DN_TEMPLATE_FIELDS = ("item_code", "item_name", "description", "uom", "conversion_factor", "rate",
    "so_detail", "against_sales_order", "income_account", "cost_center")

def _sync_goods_issue_to_delivery_note(doc, erpnext_warehouse, so_links):
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    so_names = {link[0] for link in so_links}
    if len(so_names) != 1:
        frappe.throw(_("Goods Issue {0} references more than one Sales Order; post them separately").format(doc.name))
    # One Delivery Note row per (SO item, batch, serial) group, not one summed row per SO item -
    # the same gap as the Purchase Receipt mirror above: a summed row never carried batch_no/
    # serial_no at all, so ERPNext lost which batch or serial actually shipped and submission
    # would fail outright for a batch/serial-managed item.
    groups = {}
    for row, (_so, so_item) in zip(doc.items, so_links):
        key = (so_item, row.batch_no, row.serial_no, row.stock_type)
        groups[key] = groups.get(key, 0) + flt(row.quantity)

    # See the comment on the Purchase Receipt mirror above - the whole mapping/insert/submit
    # sequence needs to run as the system user, not just the initial mapper call.
    with _as_system_user():
        dn = make_delivery_note(so_names.pop())
        template_by_so_item = {item.so_detail: item for item in dn.items}
        kept = []
        for (so_item, batch_no, serial_no, stock_type), qty in groups.items():
            template = template_by_so_item.get(so_item)
            if not template: continue
            row = dn.append("items", {})
            for field in _DN_TEMPLATE_FIELDS: row.set(field, template.get(field))
            # Same double-application risk as the Purchase Receipt path above: qty is already
            # stock-uom, so divide by the SO row's own conversion_factor before assigning it as
            # the transactional qty, and let stock_qty carry the real (stock-uom) movement.
            row.qty = qty / flt(template.conversion_factor or 1)
            row.stock_qty = qty
            row.warehouse = erpnext_warehouse
            row.batch_no = batch_no
            row.serial_no = serial_no
            row.use_serial_batch_fields = 1
            # Delivery Note Item's dimension field for its primary "warehouse" (the
            # shipping-from/source warehouse) is unprefixed, matching Stock Entry's convention.
            row.wms_stock_type = stock_type
            kept.append(row)
        if not kept: frappe.throw(_("No matching Sales Order rows found for Goods Issue {0}").format(doc.name))
        dn.items = kept
        dn.flags.ignore_permissions = True
        dn.flags.wms_managed_posting = True
        dn.insert(ignore_permissions=True)
        dn.submit()
    doc.db_set("erpnext_delivery_note", dn.name, update_modified=False)

_RETURN_PR_TEMPLATE_FIELDS = ("item_code", "item_name", "description", "uom", "conversion_factor", "rate", "purchase_receipt_item")

def _sync_goods_issue_to_return_purchase_receipt(doc, erpnext_warehouse, return_links):
    # Return to vendor: erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_return
    # builds a correctly-configured return document (is_return=1, return_against, negative qty
    # convention, party/accounts from the original) - reused purely as a template and rebuilt
    # row-for-row from what this issue actually shipped, same shape as the customer-return mirror
    # above.
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return
    pr_name = return_links[0][0]
    groups = {}
    for row, (_pr, pr_item) in zip(doc.items, return_links):
        key = (pr_item, row.batch_no, row.serial_no, row.stock_type)
        groups[key] = groups.get(key, 0) + flt(row.quantity)

    # See the comment on the Purchase Receipt mirror above - the whole mapping/insert/submit
    # sequence needs to run as the system user, not just the initial mapper call.
    with _as_system_user():
        ret = make_purchase_return(pr_name)
        template_by_pr_item = {item.purchase_receipt_item: item for item in ret.items}
        kept = []
        for (pr_item, batch_no, serial_no, stock_type), qty in groups.items():
            template = template_by_pr_item.get(pr_item)
            if not template: continue
            row = ret.append("items", {})
            for field in _RETURN_PR_TEMPLATE_FIELDS: row.set(field, template.get(field))
            row.qty = -(qty / flt(template.conversion_factor or 1))
            row.stock_qty = -qty
            row.warehouse = erpnext_warehouse
            row.batch_no = batch_no
            row.serial_no = serial_no
            row.use_serial_batch_fields = 1
            row.wms_stock_type = stock_type
            kept.append(row)
        if not kept: frappe.throw(_("No matching Purchase Receipt rows found for Goods Issue {0}").format(doc.name))
        ret.items = kept
        ret.flags.ignore_permissions = True
        ret.flags.wms_managed_posting = True
        ret.insert(ignore_permissions=True)
        ret.submit()
    doc.db_set("erpnext_purchase_receipt", ret.name, update_modified=False)

def reverse_goods_issue(doc):
    if doc.get("erpnext_delivery_note"):
        _cancel_doc("Delivery Note", doc.erpnext_delivery_note)
    elif doc.get("erpnext_purchase_receipt"):
        _cancel_doc("Purchase Receipt", doc.erpnext_purchase_receipt)
    else:
        _cancel_doc("Stock Entry", doc.get("erpnext_stock_entry"))

# --- WMS Physical Inventory Count -> Stock Entry (gain/loss) ---
#
# A Stock Reconciliation can't carry an Inventory Dimension value except on an opening
# entry (ERPNext raises ValidationError otherwise), so a dimensioned WMS Stock Type value
# would be silently unenforceable there. Post the variance as Stock Entry Material
# Receipt/Issue instead - the same document type erpnext_sync already uses for every other
# standalone stock change - which supports dimensions on ordinary transactions.

def sync_physical_inventory_count(doc):
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return None
    groups = {}
    for row in doc.items:
        key = (row.product, row.batch_no, row.serial_no, row.stock_type)
        group = groups.setdefault(key, {"variance": 0.0, "stock_uom": row.stock_uom})
        group["variance"] += flt(row.variance)

    # A count is per-bin; ERPNext only tracks stock per warehouse, so rows sharing a
    # product/batch/serial/stock_type are summed first - a group with net-zero variance
    # across its bins is pure WMS-internal bin noise and is skipped entirely.
    gains = {k: v for k, v in groups.items() if round(v["variance"], 6) > 0}
    losses = {k: v for k, v in groups.items() if round(v["variance"], 6) < 0}
    if not gains and not losses: return None, None

    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    gain_entry = loss_entry = None

    if gains:
        se = _make_stock_entry(stock_entry_type="Material Receipt", company=company, remarks=f"frappe_wms Physical Inventory Count {doc.name}")
        for (product, batch_no, serial_no, stock_type), group in gains.items():
            values = {
                "item_code": product, "qty": group["variance"], "uom": group["stock_uom"], "stock_uom": group["stock_uom"],
                "conversion_factor": 1, "batch_no": batch_no, "serial_no": serial_no, "use_serial_batch_fields": 1,
                "t_warehouse": erpnext_warehouse, "to_wms_stock_type": stock_type,
            }
            rate = _resolve_rate(product)
            if rate: values["basic_rate"] = rate
            else: values["allow_zero_valuation_rate"] = 1
            se.append("items", values)
        se.flags.wms_managed_posting = True
        _insert_and_submit_as_system(se)
        gain_entry = se.name

    if losses:
        se = _make_stock_entry(stock_entry_type="Material Issue", company=company, remarks=f"frappe_wms Physical Inventory Count {doc.name}")
        for (product, batch_no, serial_no, stock_type), group in losses.items():
            se.append("items", {
                "item_code": product, "qty": -group["variance"], "uom": group["stock_uom"], "stock_uom": group["stock_uom"],
                "conversion_factor": 1, "batch_no": batch_no, "serial_no": serial_no, "use_serial_batch_fields": 1,
                "s_warehouse": erpnext_warehouse, "wms_stock_type": stock_type,
            })
        se.flags.wms_managed_posting = True
        _insert_and_submit_as_system(se)
        loss_entry = se.name

    return gain_entry, loss_entry

# --- Kitting Order -> Stock Entry ("Repack") ---
#
# "Manufacture" was ruled out for the same reason P2's Work Order FG receipt avoided it:
# ERPNext's validate_raw_materials_exists() hard-requires it to run against a Work Order with
# real backflush rows, which a kitting order (no Work Order at all) never has. "Repack" is
# ERPNext's own purpose for an N:M item-composition change without a Work Order - the correct
# fit, not a workaround.

def sync_kitting_order(order):
    erpnext_warehouse = _erpnext_warehouse(order.warehouse)
    if not erpnext_warehouse: return None
    company = frappe.db.get_value("WMS Warehouse", order.warehouse, "company")
    kit_uom = frappe.db.get_value("WMS Product", {"item": order.kit_item}, "stock_uom") or frappe.db.get_value("Item", order.kit_item, "stock_uom")
    kit_row = frappe._dict(item=order.kit_item, quantity=order.quantity, stock_uom=kit_uom, batch_no=None, serial_no=None, stock_type="AVAILABLE")
    se = _make_stock_entry(stock_entry_type="Repack", company=company, remarks=f"frappe_wms Kitting Order {order.name}")
    if order.direction == "Assemble":
        for c in order.components:
            comp_row = frappe._dict(item=c.item, quantity=c.required_qty, stock_uom=c.stock_uom, batch_no=None, serial_no=None, stock_type="AVAILABLE")
            _append_row(se, comp_row, target_field="s_warehouse", erpnext_warehouse=erpnext_warehouse)
        _append_row(se, kit_row, target_field="t_warehouse", erpnext_warehouse=erpnext_warehouse)
    else:
        _append_row(se, kit_row, target_field="s_warehouse", erpnext_warehouse=erpnext_warehouse)
        for c in order.components:
            comp_row = frappe._dict(item=c.item, quantity=c.required_qty, stock_uom=c.stock_uom, batch_no=None, serial_no=None, stock_type="AVAILABLE")
            _append_row(se, comp_row, target_field="t_warehouse", erpnext_warehouse=erpnext_warehouse)
    # A Repack entry's incoming (t_warehouse) row(s) need their rate set manually - confirmed
    # live on both counts: ERPNext hard-requires it whenever there's more than one such row
    # (Disassemble's multi-component output), and even for a single row (Assemble's one kit
    # item) ERPNext tries to derive a rate from the consumed inputs rather than honoring
    # _append_row's own allow_zero_valuation_rate fallback, which fails the same way when the
    # inputs are themselves zero-valued. Always flip the flag rather than only when multiple.
    for row in se.items:
        if row.t_warehouse: row.set_basic_rate_manually = 1
    se.flags.wms_managed_posting = True
    _insert_and_submit_as_system(se)
    return se.name
