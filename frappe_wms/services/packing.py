import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.stock import transfer_stock

def repack(source_hu, destination_hu, items, reference_name, idempotency_key):
    source=frappe.get_doc("Handling Unit",source_hu); destination=frappe.get_doc("Handling Unit",destination_hu)
    if source.warehouse != destination.warehouse or source.current_bin != destination.current_bin: frappe.throw(_("Source and destination HUs must be in the same bin"))
    for i,item in enumerate(items,1):
        src={"warehouse":source.warehouse,"product":item["item"],"batch_no":item.get("batch_no"),"serial_no":item.get("serial_no"),"handling_unit":source.name,"storage_bin":source.current_bin,"stock_type":item["stock_type"],"stock_uom":item["stock_uom"]}
        dst={"handling_unit":destination.name,"storage_bin":destination.current_bin,"stock_type":item["stock_type"]}
        transfer_stock(source=src,destination=dst,quantity=item["quantity"],movement_type="801",reference_doctype="Packing Order",reference_name=reference_name,idempotency_key=f"{idempotency_key}:{i}")
    frappe.get_doc({"doctype":"Handling Unit Event","handling_unit":destination.name,"event_type":"Packed","bin_after":destination.current_bin,"reference_doctype":"Packing Order","reference_name":reference_name,"event_timestamp":now_datetime(),"performed_by":frappe.session.user}).insert(ignore_permissions=True)
