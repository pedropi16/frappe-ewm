import uuid
import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.stock import post_entries

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
