import uuid
import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, flt
from frappe_wms.services.stock import post_entries
from frappe_wms.services.determination import determine_process_type, determine_storage_process, matches_inspection_rule
from frappe_wms.services.storage_process import first_step
from frappe_wms.services.cross_dock import find_cross_dock_demand
from frappe_wms.services.handling_unit import get_or_create_handling_unit
from frappe_wms.services.task import my_resource, create_tasks_for_request
from frappe_wms.utils import require_role

OPEN_INBOUND_STATUSES = ("Draft", "Expected", "Arrived", "Receiving", "Partially Received")

def post_goods_receipt(doc):
    if frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype": doc.doctype, "reference_name": doc.name}): return
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
        if matches_inspection_rule(doc.warehouse, row.item, frappe.db.get_value("Item", row.item, "item_group")):
            row.db_set("stock_type", "QUALITY", update_modified=False)
            inspection_rows.append(row)
        entry = {"warehouse":doc.warehouse,"product":row.item,"batch_no":row.batch_no,"serial_no":row.serial_no,"handling_unit":row.handling_unit,"storage_bin":doc.receiving_bin,"stock_type":row.stock_type,"quantity":row.quantity,"stock_uom":row.stock_uom,"movement_type":"101","reference_line":row.name}
        if product and product.shelf_life_days:
            entry["shelf_life_expiry_date"] = add_days(getdate(doc.posting_datetime), product.shelf_life_days)
        entries.append(entry)
    # A receipt is an external increase, so post each row independently.
    for i, entry in enumerate(entries,1): post_entries([entry],doc.doctype,doc.name,f"GR:{doc.name}:{i}")
    for row in inspection_rows:
        frappe.get_doc({
            "doctype": "WMS Quality Inspection", "warehouse": doc.warehouse, "product": row.item,
            "batch_no": row.batch_no, "serial_no": row.serial_no, "handling_unit": row.handling_unit,
            "storage_bin": doc.receiving_bin, "from_stock_type": "QUALITY", "quantity": row.quantity,
            "stock_uom": row.stock_uom, "goods_receipt": doc.name,
        }).insert(ignore_permissions=True)
    doc.db_set("status","Posted")

def reverse_goods_receipt(doc):
    original=frappe.get_all("WMS Stock Ledger Entry",filters={"reference_doctype":doc.doctype,"reference_name":doc.name,"reversal_of":["in",[None,""]]},fields=["*"])
    if not original: return
    for i,row in enumerate(original,1):
        values={k:row.get(k) for k in ("warehouse","product","batch_no","serial_no","handling_unit","storage_bin","stock_type","stock_uom")}
        values.update({"quantity":-row.quantity,"movement_type":"102","reversal_of":row.name})
        post_entries([values],doc.doctype,doc.name,f"GR-REV:{doc.name}:{i}")
    doc.db_set({"status":"Reversed","reversed":1})

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
        cross_dock_matches = find_cross_dock_demand(receipt.warehouse, row.item, row.stock_type, remaining_qty)
        if cross_dock_matches:
            cross_dock_process_type = determine_process_type(receipt.warehouse, "Internal Move", item=row.item, stock_type=row.stock_type, default="INTERNAL_MOVE")
            for match in cross_dock_matches:
                cd_req = frappe.get_doc({"doctype":"Warehouse Request","request_type":"Cross Dock","warehouse":receipt.warehouse,"product":row.item,
                    "requested_quantity":match["quantity"],"stock_uom":row.stock_uom,"source_bin":receipt.receiving_bin,"source_hu":row.handling_unit,
                    "destination_bin":match["staging_bin"],"stock_type":row.stock_type,
                    "reference_doctype":"Outbound Delivery","reference_name":match["delivery"],"reference_line":match["delivery_item"],
                    "process_type":cross_dock_process_type,"priority":"High","status":"Open"})
                cd_req.insert(ignore_permissions=True); names.append(cd_req.name)
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
        req=frappe.get_doc({"doctype":"Warehouse Request","request_type":"Putaway","warehouse":receipt.warehouse,"product":row.item,"requested_quantity":remaining_qty,"stock_uom":row.stock_uom,"source_bin":receipt.receiving_bin,"source_hu":row.handling_unit,"stock_type":row.stock_type,"reference_doctype":receipt.doctype,"reference_name":receipt.name,"reference_line":row.name,"process_type":process_type,"storage_process":storage_process,"process_step":process_step,"priority":"Normal","status":"Open"})
        req.insert(ignore_permissions=True); names.append(req.name)
    return names

def list_open_inbound_deliveries(user=None):
    resource = my_resource(user)
    filters = {"status": ["in", OPEN_INBOUND_STATUSES]}
    if resource: filters["warehouse"] = resource.warehouse
    return frappe.get_list("Inbound Delivery", filters=filters,
        fields=["name", "inbound_delivery_number", "warehouse", "supplier", "receiving_bin", "status", "posting_date"],
        order_by="posting_date asc, creation asc", limit=50)

def create_and_submit_goods_receipt(inbound_delivery, items):
    # items: [{inbound_delivery_item, item, quantity, stock_uom, handling_unit, stock_type, batch_no, serial_no, hu_type}]
    # hu_type is optional when handling_unit doesn't already exist - it falls back to
    # WMS Settings.default_handling_unit_type so a scan of a fresh pallet/carton auto-registers
    # in place, same as the RF "Receive" flow described in the README.
    require_role("WMS Operator", "WMS Receiver", "WMS Supervisor")
    delivery = frappe.get_doc("Inbound Delivery", inbound_delivery)
    if not items: frappe.throw(_("At least one receipt line is required"))
    for item in items:
        item["handling_unit"] = get_or_create_handling_unit(
            item.get("handling_unit"), item.get("hu_type"), delivery.receiving_bin, delivery.warehouse,
        )
    gr = frappe.get_doc({
        "doctype": "Goods Receipt", "inbound_delivery": delivery.name, "warehouse": delivery.warehouse,
        "receiving_bin": delivery.receiving_bin, "items": items,
    })
    gr.insert(ignore_permissions=True)
    gr.flags.ignore_permissions = True
    gr.submit()
    request_names = create_putaway_requests(gr.name)
    batch_key = frappe.generate_hash(length=10)
    task_names = [create_tasks_for_request(name, batch_key=batch_key) for name in request_names]
    return {"goods_receipt": gr.name, "warehouse_requests": request_names, "warehouse_tasks": task_names}

def _production_supplier():
    # Inbound Delivery's supplier field is mandatory (it's normally an external-receiving
    # document), but an FG receipt from a Work Order has no real supplier - use a fixed
    # placeholder Supplier record for this internal case rather than weakening the field for
    # every genuine external receipt.
    name = "WMS Production (Internal)"
    if not frappe.db.exists("Supplier", name):
        frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_type": "Company"}).insert(ignore_permissions=True)
    return name

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
