import frappe
from frappe import _
from frappe_wms.services.stock import post_entries

def post_goods_issue(doc):
    if frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype":doc.doctype,"reference_name":doc.name}): return
    for i,row in enumerate(doc.items,1):
        hu=frappe.get_doc("Handling Unit",row.handling_unit)
        if hu.status not in {"Loaded","Staged"}: frappe.throw(_("HU {0} is not staged or loaded").format(hu.name))
        entry={"warehouse":doc.warehouse,"product":row.item,"batch_no":row.batch_no,"serial_no":row.serial_no,"handling_unit":row.handling_unit,"storage_bin":doc.staging_bin,"stock_type":row.stock_type,"quantity":-row.quantity,"stock_uom":row.stock_uom,"movement_type":"601","reference_line":row.name}
        post_entries([entry],doc.doctype,doc.name,f"GI:{doc.name}:{i}")
        hu.flags.wms_service_update=True; hu.status="Shipped"; hu.save(ignore_permissions=True)
    doc.db_set("status","Posted")

def reverse_goods_issue(doc):
    original=frappe.get_all("WMS Stock Ledger Entry",filters={"reference_doctype":doc.doctype,"reference_name":doc.name,"reversal_of":["in",[None,""]]},fields=["*"])
    if not original: return
    hus=set()
    for i,row in enumerate(original,1):
        values={k:row.get(k) for k in ("warehouse","product","batch_no","serial_no","handling_unit","storage_bin","stock_type","stock_uom")}
        values.update({"quantity":-row.quantity,"movement_type":"602","reversal_of":row.name})
        post_entries([values],doc.doctype,doc.name,f"GI-REV:{doc.name}:{i}")
        if row.handling_unit: hus.add(row.handling_unit)
    for hu_name in hus:
        hu=frappe.get_doc("Handling Unit",hu_name)
        hu.flags.wms_service_update=True; hu.status="Staged"; hu.save(ignore_permissions=True)
    doc.db_set({"status":"Reversed","reversed":1})
