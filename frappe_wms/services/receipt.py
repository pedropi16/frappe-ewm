import uuid
import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.services.task import my_resource, create_tasks_for_request
from frappe_wms.utils import require_role

OPEN_INBOUND_STATUSES = ("Draft", "Expected", "Arrived", "Receiving", "Partially Received")

def post_goods_receipt(doc):
    if frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype": doc.doctype, "reference_name": doc.name}): return
    entries=[]
    for row in doc.items:
        if not row.handling_unit: frappe.throw(_("Row {0}: Handling Unit is required").format(row.idx))
        entries.append({"warehouse":doc.warehouse,"product":row.item,"batch_no":row.batch_no,"serial_no":row.serial_no,"handling_unit":row.handling_unit,"storage_bin":doc.receiving_bin,"stock_type":row.stock_type,"quantity":row.quantity,"stock_uom":row.stock_uom,"movement_type":"101","reference_line":row.name})
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
    # hu_type is only required when handling_unit doesn't already exist (received onto a
    # fresh pallet/carton scanned for the first time) - it is then auto-created in place.
    require_role("WMS Operator", "WMS Receiver", "WMS Supervisor")
    delivery = frappe.get_doc("Inbound Delivery", inbound_delivery)
    if not items: frappe.throw(_("At least one receipt line is required"))
    for item in items:
        if not frappe.db.exists("Handling Unit", item.get("handling_unit")):
            if not item.get("hu_type"): frappe.throw(_("Handling Unit {0} does not exist; specify a Handling Unit Type to create it").format(item.get("handling_unit")))
            frappe.get_doc({
                "doctype": "Handling Unit", "hu_number": item["handling_unit"], "hu_type": item["hu_type"],
                "warehouse": delivery.warehouse, "current_bin": delivery.receiving_bin, "status": "Open",
            }).insert(ignore_permissions=True)
    gr = frappe.get_doc({
        "doctype": "Goods Receipt", "inbound_delivery": delivery.name, "warehouse": delivery.warehouse,
        "receiving_bin": delivery.receiving_bin, "items": items,
    })
    gr.insert(ignore_permissions=True)
    gr.flags.ignore_permissions = True
    gr.submit()
    request_names = create_putaway_requests(gr.name)
    task_names = [create_tasks_for_request(name) for name in request_names]
    return {"goods_receipt": gr.name, "warehouse_requests": request_names, "warehouse_tasks": task_names}
