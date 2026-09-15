import frappe
from frappe import _
from frappe_wms.services.task import confirm_task as _confirm_task
from frappe_wms.services.packing import repack as _repack
from frappe_wms.utils import parse_json

@frappe.whitelist()
def get_task(task_name):
    doc=frappe.get_doc("Warehouse Task",task_name); doc.check_permission("read")
    return doc.as_dict()

@frappe.whitelist()
def confirm_task(task_name, scanned_source=None, scanned_destination=None, confirmed_quantity=None, destination_hu=None, device=None, idempotency_key=None):
    return _confirm_task(task_name,scanned_source,scanned_destination,confirmed_quantity,destination_hu,device,idempotency_key)

@frappe.whitelist()
def hu_overview(hu_number):
    hu=frappe.get_doc("Handling Unit",hu_number); hu.check_permission("read")
    stock=frappe.get_all("WMS Stock Balance",filters={"handling_unit":hu.name,"quantity":[">",0]},fields=["product","batch_no","serial_no","stock_type","quantity","stock_uom","storage_bin"])
    children=frappe.get_all("Handling Unit",filters={"parent_hu":hu.name},fields=["name","hu_type","status","current_bin"])
    return {"handling_unit":hu.as_dict(),"stock":stock,"children":children}

@frappe.whitelist()
def bin_overview(bin_code):
    bin_doc=frappe.get_doc("Storage Bin",bin_code); bin_doc.check_permission("read")
    stock=frappe.get_all("WMS Stock Balance",filters={"storage_bin":bin_doc.name,"quantity":[">",0]},fields=["product","handling_unit","batch_no","serial_no","stock_type","quantity","stock_uom"])
    return {"storage_bin":bin_doc.as_dict(),"stock":stock}

@frappe.whitelist()
def repack(source_hu,destination_hu,items,packing_order,idempotency_key):
    return _repack(source_hu,destination_hu,parse_json(items,"items"),packing_order,idempotency_key)
