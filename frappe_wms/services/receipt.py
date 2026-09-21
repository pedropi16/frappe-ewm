import uuid
import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.services.handling_unit import get_or_create_handling_unit
from frappe_wms.services.task import my_resource, create_tasks_for_request
from frappe_wms.utils import require_role

OPEN_INBOUND_STATUSES = ("Draft", "Expected", "Arrived", "Receiving", "Partially Received")

def post_goods_receipt(doc):
    if frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype": doc.doctype, "reference_name": doc.name}): return
    entries=[]
    for row in doc.items:
        if not row.handling_unit: frappe.throw(_("Row {0}: Handling Unit is required").format(row.idx))
        product = frappe.get_cached_doc("WMS Product", row.item) if frappe.db.exists("WMS Product", row.item) else None
        if product and product.warehouse_managed:
            if product.serial_control in ("Required at Receipt", "Always") and not row.serial_no:
                frappe.throw(_("Row {0}: {1} requires a serial number at receipt").format(row.idx, row.item))
            if product.batch_control and not row.batch_no:
                frappe.throw(_("Row {0}: {1} requires a batch number at receipt").format(row.idx, row.item))
        entry = {"warehouse":doc.warehouse,"product":row.item,"batch_no":row.batch_no,"serial_no":row.serial_no,"handling_unit":row.handling_unit,"storage_bin":doc.receiving_bin,"stock_type":row.stock_type,"quantity":row.quantity,"stock_uom":row.stock_uom,"movement_type":"101","reference_line":row.name}
        if product and product.shelf_life_days:
            entry["shelf_life_expiry_date"] = add_days(getdate(doc.posting_datetime), product.shelf_life_days)
        entries.append(entry)
    # A receipt is an external increase, so post each row independently.
    for i, entry in enumerate(entries,1): post_entries([entry],doc.doctype,doc.name,f"GR:{doc.name}:{i}")
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
    for row in receipt.items:
        req=frappe.get_doc({"doctype":"Warehouse Request","request_type":"Putaway","warehouse":receipt.warehouse,"product":row.item,"requested_quantity":row.quantity,"stock_uom":row.stock_uom,"source_bin":receipt.receiving_bin,"source_hu":row.handling_unit,"stock_type":row.stock_type,"reference_doctype":receipt.doctype,"reference_name":receipt.name,"reference_line":row.name,"process_type":"GR_PUTAWAY","priority":"Normal","status":"Open"})
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
