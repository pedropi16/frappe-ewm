import uuid
import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, flt
from frappe_wms.services.stock import post_entries
from frappe_wms.services.determination import determine_process_type, determine_storage_process, matches_inspection_rule
from frappe_wms.services.storage_process import first_step
from frappe_wms.services.cross_dock import find_cross_dock_demand, reserve_cross_dock_demand, planned_matches, consume_plan
from frappe_wms.services.handling_unit import get_or_create_handling_unit
from frappe_wms.services.task import my_resource, create_tasks_for_request, OPEN_TASK_STATUSES, TASK_SUMMARY_FIELDS
from frappe_wms.utils import require_role

OPEN_INBOUND_STATUSES = ("Draft", "Expected", "Arrived", "Receiving", "Partially Received")

# Same grouping the RF menu uses for its "Inbound" section (screens/shared.js TASK_TYPE_GROUPS.inbound).
INBOUND_TASK_TYPES = ("Unload", "Putaway", "Deconsolidation", "Cross Dock")

def find_putaway_tasks(reference):
    # Mirrors picking.find_pick_tasks for the inbound side: jump straight into the task-confirm
    # wizard by scanning the Warehouse Order, Warehouse Request, Warehouse Task, Queue, or the
    # Handling Unit the operator is holding (the common case right after receiving) - no browsing
    # the full open-tasks list required.
    require_role("WMS Operator", "WMS Receiver", "WMS Supervisor")
    reference = (reference or "").strip()
    if not reference:
        frappe.throw(_("Scan or enter a Warehouse Order, Warehouse Request, Warehouse Task, Queue, or Handling Unit"))
    resource = my_resource()
    base_filters = {"task_type": ["in", INBOUND_TASK_TYPES], "status": ["in", OPEN_TASK_STATUSES], "docstatus": 0}
    if resource: base_filters["warehouse"] = resource.warehouse

    if frappe.db.exists("Warehouse Task", reference):
        tasks = frappe.get_all("Warehouse Task", filters={**base_filters, "name": reference}, fields=TASK_SUMMARY_FIELDS)
    elif frappe.db.exists("Warehouse Order", reference):
        tasks = frappe.get_all("Warehouse Task", filters={**base_filters, "warehouse_order": reference}, fields=TASK_SUMMARY_FIELDS)
    elif frappe.db.exists("Warehouse Request", reference):
        tasks = frappe.get_all("Warehouse Task", filters={**base_filters, "warehouse_request": reference}, fields=TASK_SUMMARY_FIELDS)
    elif frappe.db.exists("Warehouse Queue", reference):
        tasks = frappe.get_all("Warehouse Task", filters={**base_filters, "queue": reference}, fields=TASK_SUMMARY_FIELDS)
    else:
        by_source = frappe.get_all("Warehouse Task", filters={**base_filters, "source_hu": reference}, fields=TASK_SUMMARY_FIELDS)
        by_dest = frappe.get_all("Warehouse Task", filters={**base_filters, "destination_hu": reference}, fields=TASK_SUMMARY_FIELDS)
        seen = {t.name for t in by_source}
        tasks = by_source + [t for t in by_dest if t.name not in seen]
    return sorted(tasks, key=lambda t: (t.sequence or 0, t.name))

def post_goods_receipt(doc):
    if frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype": doc.doctype, "reference_name": doc.name}): return
    # Receipt progress moves on submit for every Goods Receipt - API, RF or a plain desk
    # submit - exactly as reverse_goods_receipt moves it back on cancel; it used to move only on
    # the API path, so a desk-submitted receipt was never counted against its delivery.
    if doc.inbound_delivery:
        delivery = _lock_inbound_delivery(doc.inbound_delivery)
        if delivery.docstatus == 2: frappe.throw(_("Inbound Delivery {0} is cancelled").format(delivery.name))
        _validate_receipt_quantities(delivery, doc.items)
    entries=[]
    inspection_rows=[]
    for row in doc.items:
        if not row.handling_unit: frappe.throw(_("Row {0}: Handling Unit is required").format(row.idx))
        product = frappe.get_cached_doc("WMS Product", row.item) if frappe.db.exists("WMS Product", row.item) else None
        if product and product.warehouse_managed:
            if product.serial_control in ("Required at Receipt", "Always") and not row.serial_no:
                frappe.throw(_("Row {0}: {1} requires a serial number at receipt").format(row.idx, row.item))
            if product.batch_control and not row.batch_no:
                frappe.throw(_("Row {0}: {1} requires a batch number at receipt").format(row.idx, row.item))
        # A matching Inspection Rule routes the receipt into QUALITY instead of its normal
        # stock type - persisted onto the row itself (not just this posting's ledger entry) so
        # every downstream reader (putaway, ERPNext dimension mirroring) sees the same thing.
        # A row already landing in QUALITY some other way (e.g. a customer return, which always
        # receives into QUALITY regardless of whether any Inspection Rule is configured for that
        # item/warehouse - see create_return_inbound_delivery) gets the same inspection record;
        # previously only a rule match ever created one, silently leaving a manually-QUALITY'd
        # receipt with no inspection to act on.
        if matches_inspection_rule(doc.warehouse, row.item, frappe.db.get_value("Item", row.item, "item_group")):
            row.db_set("stock_type", "QUALITY", update_modified=False)
        if row.stock_type == "QUALITY":
            inspection_rows.append(row)
        entry = {"warehouse":doc.warehouse,"product":row.item,"batch_no":row.batch_no,"serial_no":row.serial_no,"handling_unit":row.handling_unit,"storage_bin":doc.receiving_bin,"stock_type":row.stock_type,"quantity":row.quantity,"stock_uom":row.stock_uom,"movement_type":"101","reference_line":row.name}
        if product and product.shelf_life_days:
            entry["shelf_life_expiry_date"] = add_days(getdate(doc.posting_datetime), product.shelf_life_days)
        entries.append(entry)
    # A receipt is an external increase, so post each row independently.
    for i, entry in enumerate(entries,1): post_entries([entry],doc.doctype,doc.name,f"GR:{doc.name}:{i}")
    if doc.inbound_delivery:
        _update_inbound_delivery_receipt_progress(doc.inbound_delivery, doc.items)
    for row in inspection_rows:
        rule = matches_inspection_rule(doc.warehouse, row.item, frappe.db.get_value("Item", row.item, "item_group"))
        sampling, acceptable = frappe.db.get_value("Inspection Rule", rule, ["sampling_percentage", "acceptable_failures"]) if rule else (0, 0)
        qi = frappe.get_doc({
            "doctype": "WMS Quality Inspection", "warehouse": doc.warehouse, "product": row.item,
            "batch_no": row.batch_no, "serial_no": row.serial_no, "handling_unit": row.handling_unit,
            "storage_bin": doc.receiving_bin, "from_stock_type": "QUALITY", "quantity": row.quantity,
            "stock_uom": row.stock_uom, "goods_receipt": doc.name, "sampling_percentage": sampling or 100, "acceptable_failures": acceptable or 0,
        }).insert(ignore_permissions=True)
        from frappe_wms.services.quality import generate_samples
        generate_samples(qi)
    doc.db_set("status","Posted")

def reverse_goods_receipt(doc):
    from frappe_wms.services.archiving import ensure_reversible; ensure_reversible(doc)
    original=frappe.get_all("WMS Stock Ledger Entry",filters={"reference_doctype":doc.doctype,"reference_name":doc.name,"reversal_of":["in",[None,""]]},fields=["*"])
    if not original: return
    for i,row in enumerate(original,1):
        values={k:row.get(k) for k in ("warehouse","product","batch_no","serial_no","handling_unit","storage_bin","stock_type","stock_uom")}
        values.update({"quantity":-row.quantity,"movement_type":"102","reversal_of":row.name})
        post_entries([values],doc.doctype,doc.name,f"GR-REV:{doc.name}:{i}")
    doc.db_set({"status":"Reversed","reversed":1})
    # Hand the reversed quantity back to the delivery line, or it would stay "Received" forever
    # and the RF Receive screen would never offer it again for the corrected receipt.
    if doc.inbound_delivery:
        _update_inbound_delivery_receipt_progress(doc.inbound_delivery, doc.items, sign=-1)

def create_putaway_requests(receipt_name):
    receipt=frappe.get_doc("Goods Receipt",receipt_name)
    if receipt.docstatus != 1: frappe.throw(_("Goods Receipt must be submitted"))
    names=[]
    source_storage_type = frappe.db.get_value("Storage Bin", receipt.receiving_bin, "storage_type")
    for row in receipt.items:
        # Opportunistic cross-docking: any portion of this row that matches open outbound
        # demand skips putaway entirely and routes straight to the matched delivery's
        # staging bin instead - a "Cross Dock" request/task, not a Putaway one. Reuses the
        # "Internal Move" process-type determination since physically it's the same kind of
        # bin-to-bin transfer; only the request/task type label is distinct, for traceability.
        remaining_qty = flt(row.quantity)
        # Planned cross-docking first: demand reserved for the expected delivery before the goods arrived (plan_cross_dock) - already reserved, so only routed here.
        planned = planned_matches(row.inbound_delivery_item, remaining_qty) if row.inbound_delivery_item else []
        cross_dock_matches = [m for _plan, m in planned] + find_cross_dock_demand(receipt.warehouse, row.item, row.stock_type, remaining_qty - sum(m["quantity"] for _plan, m in planned))
        planned_by_match = {id(m): plan for plan, m in planned}
        if cross_dock_matches:
            cross_dock_process_type = determine_process_type(receipt.warehouse, "Internal Move", item=row.item, stock_type=row.stock_type, default="INTERNAL_MOVE")
            for match in cross_dock_matches:
                cd_req = frappe.get_doc({"doctype":"Warehouse Request","request_type":"Cross Dock","warehouse":receipt.warehouse,"product":row.item,
                    "requested_quantity":match["quantity"],"stock_uom":row.stock_uom,"source_bin":receipt.receiving_bin,"source_hu":row.handling_unit,
                    "destination_bin":match["staging_bin"],"stock_type":row.stock_type,"batch_no":row.batch_no,"serial_no":row.serial_no,
                    "reference_doctype":"Outbound Delivery","reference_name":match["delivery"],"reference_line":match["delivery_item"],
                    "process_type":cross_dock_process_type,"priority":"High","status":"Open"})
                cd_req.insert(ignore_permissions=True); names.append(cd_req.name)
                if id(match) in planned_by_match: consume_plan(planned_by_match[id(match)], match["quantity"])
                else: reserve_cross_dock_demand(match)
                remaining_qty -= match["quantity"]
        if remaining_qty <= 0: continue  # fully cross-docked - no Putaway request for this row

        process_type = determine_process_type(receipt.warehouse, "Putaway", item=row.item, stock_type=row.stock_type, default="GR_PUTAWAY")
        # Opt-in: only receipts whose warehouse has a matching Process Determination Rule get
        # routed through a multi-step Storage Process. No rule configured (the common case
        # today) falls straight back to the flat single-request Putaway this always did.
        storage_process, process_step = None, None
        try:
            storage_process = determine_storage_process({
                "warehouse": receipt.warehouse, "document_type": "Inbound Delivery", "item": row.item,
                "item_group": frappe.db.get_value("Item", row.item, "item_group"), "stock_type": row.stock_type,
                "source_storage_type": source_storage_type,
            })
        except frappe.ValidationError:
            storage_process = None
        if storage_process:
            step = first_step(storage_process)
            if step:
                process_type = step.process_type
                process_step = step.step_code
        req=frappe.get_doc({"doctype":"Warehouse Request","request_type":"Putaway","warehouse":receipt.warehouse,"product":row.item,"requested_quantity":remaining_qty,"stock_uom":row.stock_uom,"source_bin":receipt.receiving_bin,"source_hu":row.handling_unit,"stock_type":row.stock_type,"batch_no":row.batch_no,"serial_no":row.serial_no,"reference_doctype":receipt.doctype,"reference_name":receipt.name,"reference_line":row.name,"process_type":process_type,"storage_process":storage_process,"process_step":process_step,"priority":"Normal","status":"Open"})
        req.insert(ignore_permissions=True); names.append(req.name)
    return names

def list_open_inbound_deliveries(user=None):
    resource = my_resource(user)
    filters = {"status": ["in", OPEN_INBOUND_STATUSES]}
    if resource: filters["warehouse"] = resource.warehouse
    return frappe.get_list("Inbound Delivery", filters=filters,
        fields=["name", "inbound_delivery_number", "warehouse", "supplier", "external_reference", "receiving_bin", "status", "posting_date"],
        order_by="posting_date asc, creation asc", limit=50)

def receiving_worklist(inbound_delivery):
    """Everything the RF scan-first Receive screen needs in one call: each open line with what is
    left to receive, the item's barcodes (so a scan resolves on the device, no round trip), and
    whether posting will demand a batch or serial - the same rules post_goods_receipt enforces."""
    doc = frappe.get_doc("Inbound Delivery", inbound_delivery)
    doc.check_permission("read")
    lines = []
    for row in doc.items:
        remaining = flt(row.expected_quantity) - flt(row.received_quantity)
        if remaining <= 0.000001: continue
        product = frappe.db.get_value("WMS Product", row.item, ["warehouse_managed", "batch_control", "serial_control"], as_dict=True) or {}
        managed = bool(product.get("warehouse_managed"))
        lines.append({
            "inbound_delivery_item": row.name, "line_number": row.line_number, "item": row.item,
            "item_name": row.item_name or frappe.db.get_value("Item", row.item, "item_name"),
            "remaining": remaining, "stock_uom": row.stock_uom, "stock_type": row.expected_stock_type,
            "barcodes": frappe.get_all("Item Barcode", filters={"parent": row.item}, pluck="barcode"),
            # Units the operator may count in (cases, pallets...), each with its factor to the
            # stock UOM - the ERPNext Item's UOM conversions, the order line's UOM first.
            "uoms": _receiving_uoms(row),
            "batch_required": bool(managed and product.get("batch_control")),
            "serial_required": bool(managed and product.get("serial_control") in ("Required at Receipt", "Always")),
        })
    return {
        "name": doc.name, "inbound_delivery_number": doc.inbound_delivery_number, "supplier": doc.supplier,
        "external_reference": doc.external_reference, "receiving_bin": doc.receiving_bin, "status": doc.status, "lines": lines,
        "hu_types": frappe.get_all("Handling Unit Type", filters={"active": 1}, fields=["name", "numbering_mode"]),
        "default_hu_type": frappe.db.get_single_value("WMS Settings", "default_handling_unit_type"),
    }

def _receiving_uoms(row):
    from frappe_wms.services.uom import unit_options
    return unit_options(row.item, row.stock_uom, row.get("uom"), row.get("conversion_factor"))

def _get_or_create_batch(item_code, batch_no):
    # Same "a scan of something new registers it in place" idiom as get_or_create_handling_unit -
    # a real incoming batch is, by definition, usually one nobody has entered into the system
    # before. Without this, the RF Receive screen's batch field could only ever accept an
    # already-registered Batch (reproduced live: a freshly-typed batch number threw
    # LinkValidationError deep inside the ERPNext Purchase Receipt mirror, with no way for the
    # operator to register it from this screen at all - the same gap the HU field doesn't have).
    if not batch_no or frappe.db.exists("Batch", batch_no): return batch_no
    frappe.get_doc({"doctype": "Batch", "item": item_code, "batch_id": batch_no}).insert(ignore_permissions=True)
    return batch_no

def _get_or_create_serial_no(item_code, serial_no):
    # Same reasoning as _get_or_create_batch, for serial-controlled items.
    if not serial_no or frappe.db.exists("Serial No", serial_no): return serial_no
    frappe.get_doc({"doctype": "Serial No", "item_code": item_code, "serial_no": serial_no}).insert(ignore_permissions=True)
    return serial_no

def _quantities_by_line(items):
    by_row = {}
    for item in items:
        row_name = item.get("inbound_delivery_item")
        if row_name: by_row[row_name] = by_row.get(row_name, 0) + flt(item.get("quantity"))
    return by_row

def _lock_inbound_delivery(delivery_name):
    # for_update=True, not a separate "select ... for update" followed by a plain get_doc: Frappe
    # runs MariaDB at REPEATABLE READ, where a plain read after waiting on a row lock still returns
    # the transaction's older snapshot - a second receiver's request would lock the row, then read
    # received_quantity as it was *before* the first receiver's receipt committed. get_doc's own
    # for_update reads the parent and its child rows with locking (current) reads.
    return frappe.get_doc("Inbound Delivery", delivery_name, for_update=True)

def _validate_receipt_quantities(delivery, items):
    # The RF Receive screen refuses more than a line's remaining quantity, but nothing server-side
    # did - and two receivers on the same truck (the normal case at a busy dock) both saw the same
    # remaining quantity and both posted it. Reproduced under concurrent load: a 48-unit line
    # ended up with 170 received across four Goods Receipts, and the ERPNext Purchase Receipt
    # mirror then failed later with an unrelated-looking OverAllowanceError. Must run under
    # _lock_inbound_delivery so "remaining" can't change between this check and the posting.
    rows = {row.name: row for row in delivery.items}
    for row_name, qty in _quantities_by_line(items).items():
        row = rows.get(row_name)
        if not row:
            frappe.throw(_("Line {0} does not belong to Inbound Delivery {1}").format(row_name, delivery.name))
        if row.status == "Cancelled":
            frappe.throw(_("Line {0} ({1}) is cancelled").format(row.line_number, row.item))
        remaining = flt(row.expected_quantity) - flt(row.received_quantity)
        if qty - remaining > 0.000001:
            frappe.throw(_("Line {0} ({1}): only {2} {3} left to receive, {4} scanned - it may have just been received on another scanner. Refresh the delivery.").format(
                row.line_number, row.item, frappe.format(max(remaining, 0), "Float"), row.stock_uom, frappe.format(qty, "Float")))
    for item in items:
        row = rows.get(item.get("inbound_delivery_item"))
        if row and item.get("item") and item.get("item") != row.item:
            frappe.throw(_("Line {0} is for {1}, not {2}").format(row.line_number, row.item, item.get("item")))

def _update_inbound_delivery_receipt_progress(delivery_name, items, sign=1):
    # Confirmed live in production: without this, an Inbound Delivery's own received_quantity/
    # status never move off their initial values no matter how many Goods Receipts post against
    # it - the RF Receive screen's own line filter (remaining = expected_quantity -
    # received_quantity, see receive.js) then keeps re-offering an already-fully-received line as
    # open work forever, and a repeat attempt eventually fails deep inside the ERPNext mirror with
    # a confusing "No matching Purchase Order rows found" once the underlying PO is actually
    # exhausted - the one place this bug was ever visible, and only long after the real cause.
    # sign=-1 undoes a cancelled Goods Receipt's quantities, so its lines become receivable again.
    received_by_row = _quantities_by_line(items)
    if not received_by_row: return
    # frappe.db.set_value throughout, never doc.save(): Inbound Delivery is itself submittable,
    # and a receipt against an already-submitted one (rare, but confirmed to exist in production -
    # INB-00000001) hit Frappe's own "not allowed to change Status after submission" guard on a
    # plain .save() - these are tracking-only fields, not something submission should ever lock.
    # Read under the row lock (current values), not a plain get_all - see _lock_inbound_delivery.
    delivery = _lock_inbound_delivery(delivery_name)
    all_received, any_received = True, False
    for row in delivery.items:
        new_received = max(flt(row.received_quantity) + sign * received_by_row.get(row.name, 0), 0)
        if row.name in received_by_row:
            new_status = "Received" if new_received >= flt(row.expected_quantity) else ("Partially Received" if new_received > 0 else "Open")
            frappe.db.set_value("Inbound Delivery Item", row.name, {"received_quantity": new_received, "status": new_status}, update_modified=False)
        all_received = all_received and new_received >= flt(row.expected_quantity)
        any_received = any_received or new_received > 0
    if all_received:
        frappe.db.set_value("Inbound Delivery", delivery_name, {"receipt_status": "Fully Received", "status": "Received"}, update_modified=False)
    elif any_received:
        frappe.db.set_value("Inbound Delivery", delivery_name, {"receipt_status": "Partially Received", "status": "Partially Received"}, update_modified=False)
    else:
        frappe.db.set_value("Inbound Delivery", delivery_name, {"receipt_status": "Not Received",
            "status": "Expected" if delivery.docstatus == 1 else "Draft"}, update_modified=False)

def create_and_submit_goods_receipt(inbound_delivery, items):
    # items: [{inbound_delivery_item, item, quantity, stock_uom, handling_unit, stock_type, batch_no, serial_no, hu_type}]
    # hu_type is optional when handling_unit doesn't already exist - it falls back to
    # WMS Settings.default_handling_unit_type so a scan of a fresh pallet/carton auto-registers
    # in place, same as the RF "Receive" flow described in the README.
    require_role("WMS Operator", "WMS Receiver", "WMS Supervisor")
    if not items: frappe.throw(_("At least one receipt line is required"))
    # Serializes every receipt against this delivery (see _lock_inbound_delivery) for the rest of
    # the request, so the remaining-quantity check below and the progress update after posting
    # see the same, current numbers.
    delivery = _lock_inbound_delivery(inbound_delivery)
    if delivery.docstatus == 2: frappe.throw(_("Inbound Delivery {0} is cancelled").format(delivery.name))
    for item in items:
        if flt(item.get("quantity")) <= 0: frappe.throw(_("Receipt quantity for {0} must be greater than zero").format(item.get("item")))
    _validate_receipt_quantities(delivery, items)
    for item in items:
        item["handling_unit"] = get_or_create_handling_unit(
            item.get("handling_unit"), item.get("hu_type"), delivery.receiving_bin, delivery.warehouse,
        )
        item["batch_no"] = _get_or_create_batch(item["item"], item.get("batch_no"))
        item["serial_no"] = _get_or_create_serial_no(item["item"], item.get("serial_no"))
    gr = frappe.get_doc({
        "doctype": "Goods Receipt", "inbound_delivery": delivery.name, "warehouse": delivery.warehouse,
        "receiving_bin": delivery.receiving_bin, "items": items,
    })
    gr.insert(ignore_permissions=True)
    gr.flags.ignore_permissions = True
    gr.submit()
    request_names = create_putaway_requests(gr.name)
    # The goods are received either way; a putaway with no free bin waits in the Monitor.
    from frappe_wms.services.task import plan_requests
    task_names, unplanned = plan_requests(request_names, batch_key=frappe.generate_hash(length=10))
    return {"goods_receipt": gr.name, "warehouse_requests": request_names, "warehouse_tasks": task_names, "unplanned_requests": unplanned}

def _production_supplier():
    # Inbound Delivery's supplier field is mandatory (it's normally an external-receiving
    # document), but an FG receipt from a Work Order has no real supplier - use a fixed
    # placeholder Supplier record for this internal case rather than weakening the field for
    # every genuine external receipt.
    name = "WMS Production (Internal)"
    if not frappe.db.exists("Supplier", name):
        frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_type": "Company"}).insert(ignore_permissions=True)
    return name

def _customer_returns_supplier():
    # Same shape as _production_supplier above: Inbound Delivery's supplier field is mandatory,
    # but a customer return has no supplier at all - a fixed placeholder stands in; the real
    # party (the customer) lives on the mirrored ERPNext return Delivery Note, derived from the
    # original Delivery Note it returns against.
    name = "WMS Customer Returns (Internal)"
    if not frappe.db.exists("Supplier", name):
        frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_type": "Company"}).insert(ignore_permissions=True)
    return name

def create_return_inbound_delivery(delivery_note, warehouse):
    # Customer return: physically received like any other delivery via the ordinary RF Receive
    # flow (create_and_submit_goods_receipt) against this Inbound Delivery - the only difference
    # is every line is forced into QUALITY so it always gets inspected before restock/scrap
    # (matching SAP EWM's own returns process: a returns delivery's GR always creates a Quality
    # Inspection), and it carries source_document_type/number/line so the mirror
    # (erpnext_sync._sync_goods_receipt_to_return_delivery_note) can build a proper Sales Return
    # against the original Delivery Note row-for-row, instead of an unlinked standalone receipt.
    require_role("WMS Operator", "WMS Receiver", "WMS Supervisor")
    # for_update: two returns raised for the same Delivery Note at once must not both see the
    # same "still returnable" quantity.
    dn = frappe.get_doc("Delivery Note", delivery_note, for_update=True)
    if dn.docstatus != 1: frappe.throw(_("Delivery Note must be submitted before it can be returned"))
    if dn.is_return: frappe.throw(_("Delivery Note {0} is itself a return").format(dn.name))
    wh = frappe.get_doc("WMS Warehouse", warehouse)
    if not wh.default_receiving_bin: frappe.throw(_("WMS Warehouse {0} has no default receiving bin configured").format(warehouse))
    # Only what is still returnable: the row's quantity less what ERPNext has already taken back
    # (returned_qty) and less what another open return delivery is still expecting for it.
    # Previously every line went in at its full original quantity, so a second return of the same
    # Delivery Note was accepted, received on the RF app, and only then rejected by ERPNext's own
    # StockOverReturnError - on every attempt, leaving an unreceivable delivery in every
    # receiver's list (reproduced in a simulated shift: 888 failed receipts from 20 returns).
    items = []
    for row in dn.items:
        pending = frappe.db.sql("""
            select coalesce(sum(greatest(i.expected_quantity - ifnull(i.received_quantity, 0), 0)), 0)
            from `tabInbound Delivery Item` i join `tabInbound Delivery` d on d.name = i.parent
            where i.source_document_type = 'Delivery Note' and i.source_document_line = %s
              and d.docstatus < 2 and d.status not in ('Cancelled', 'Completed')""", row.name)[0][0]
        # stock units throughout: WMS quantities are stock-UOM, and ERPNext keeps returned_qty in
        # stock_qty terms (the return mirror divides back by the row's conversion_factor itself)
        returnable = flt(row.stock_qty or row.qty) - flt(row.returned_qty) - flt(pending)
        if returnable <= 0: continue
        items.append({
            "line_number": len(items) + 1, "item": row.item_code, "expected_quantity": returnable, "stock_uom": row.stock_uom,
            "expected_stock_type": "QUALITY", "source_document_type": "Delivery Note",
            "source_document_number": dn.name, "source_document_line": row.name,
        })
    if not items: frappe.throw(_("Nothing left to return on Delivery Note {0} - it has already been returned, or a return for it is still open").format(dn.name))
    ind = frappe.get_doc({
        "doctype": "Inbound Delivery", "inbound_delivery_number": f"{dn.name}-RET-{frappe.generate_hash(length=4)}",
        "warehouse": warehouse, "company": dn.company, "supplier": _customer_returns_supplier(),
        # the placeholder supplier says nothing about who is sending it back - the receiving list shows this
        "external_reference": _("Return from {0}").format(dn.customer_name or dn.customer),
        "receiving_bin": wh.default_receiving_bin, "items": items,
    })
    ind.insert(ignore_permissions=True)
    return ind.name

def create_fg_receipt_from_work_order(work_order_name, warehouse, quantity, handling_unit, hu_type=None,
        batch_no=None, serial_no=None, stock_type="AVAILABLE"):
    # Production supply, FG receipt (production -> warehouse): a normal Goods Receipt/putaway
    # flow, just sourced from a Work Order rather than a Purchase Order - reuses the same
    # generic source_document_type/source_document_number fields Inbound Delivery Item
    # already carries for exactly this, instead of a parallel doctype.
    require_role("WMS Operator", "WMS Receiver", "WMS Supervisor")
    wo = frappe.get_doc("Work Order", work_order_name)
    receiving_bin = frappe.db.get_value("WMS Warehouse", warehouse, "default_receiving_bin")
    if not receiving_bin: frappe.throw(_("Warehouse {0} has no default receiving bin configured").format(warehouse))
    stock_uom = frappe.db.get_value("WMS Product", {"item": wo.production_item}, "stock_uom") or frappe.db.get_value("Item", wo.production_item, "stock_uom")
    handling_unit = get_or_create_handling_unit(handling_unit, hu_type, receiving_bin, warehouse)
    ind = frappe.get_doc({
        "doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": warehouse,
        "company": wo.company, "supplier": _production_supplier(),
        "receiving_bin": receiving_bin, "items": [{
            "line_number": 1, "item": wo.production_item, "expected_quantity": quantity, "stock_uom": stock_uom,
            "expected_stock_type": stock_type, "source_document_type": "Work Order", "source_document_number": work_order_name,
        }],
    })
    ind.insert(ignore_permissions=True)
    gr = frappe.get_doc({
        "doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": warehouse, "receiving_bin": receiving_bin,
        "items": [{
            "inbound_delivery_item": ind.items[0].name, "item": wo.production_item, "quantity": quantity, "stock_uom": stock_uom,
            "handling_unit": handling_unit, "stock_type": stock_type, "batch_no": batch_no, "serial_no": serial_no,
        }],
    })
    gr.insert(ignore_permissions=True)
    gr.flags.ignore_permissions = True
    gr.submit()
    request_names = create_putaway_requests(gr.name)
    batch_key = frappe.generate_hash(length=10)
    task_names = [create_tasks_for_request(name, batch_key=batch_key) for name in request_names]
    return {"goods_receipt": gr.name, "warehouse_requests": request_names, "warehouse_tasks": task_names}
