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

def complete_packing_order(packing_order_name):
    # Packing Order only records which HUs are involved, not a per-item/qty breakdown, so the
    # simple and common case this automates is "move everything out of the source HU into the
    # destination HU". Multi-HU consolidation needs an explicit item/qty plan, so it is left to
    # direct repack() calls (e.g. from the scanner) rather than guessed here.
    order = frappe.get_doc("Packing Order", packing_order_name)
    if order.status not in {"Draft", "Open", "In Process"}: frappe.throw(_("Packing order is not open"))
    if len(order.source_hus) != 1 or len(order.destination_hus) != 1:
        frappe.throw(_("Automatic packing supports exactly one source HU and one destination HU. Use the scanner repack API to plan a multi-HU consolidation."))
    source_hu = order.source_hus[0].handling_unit
    destination_hu = order.destination_hus[0].handling_unit
    balances = frappe.get_all("WMS Stock Balance", filters={"handling_unit": source_hu, "quantity": [">", 0]}, fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"])
    if not balances: frappe.throw(_("Source HU {0} has no stock to pack").format(source_hu))
    items = [{"item": b.product, "batch_no": b.batch_no, "serial_no": b.serial_no, "stock_type": b.stock_type, "quantity": b.quantity, "stock_uom": b.stock_uom} for b in balances]
    repack(source_hu, destination_hu, items, packing_order_name, f"PACK:{packing_order_name}")
    order.db_set({"status": "Completed", "completed_at": now_datetime(), "verified_by": frappe.session.user})
    return {"packing_order": order.name, "status": "Completed"}
