import frappe
from frappe import _
from frappe_wms.services.task import confirm_task as _confirm_task, list_my_tasks as _list_my_tasks, raise_exception as _raise_exception, reverse_task as _reverse_task, create_and_confirm_move as _create_and_confirm_move
from frappe_wms.services.packing import repack as _repack, complete_packing_order as _complete_packing_order, list_open_packing_orders as _list_open_packing_orders
from frappe_wms.utils import parse_json

@frappe.whitelist()
def get_task(task_name):
    doc=frappe.get_doc("Warehouse Task",task_name); doc.check_permission("read")
    return doc.as_dict()

@frappe.whitelist()
def my_tasks():
    return _list_my_tasks()

@frappe.whitelist()
def confirm_task(task_name, scanned_source=None, scanned_destination=None, confirmed_quantity=None, destination_hu=None, device=None, idempotency_key=None):
    return _confirm_task(task_name,scanned_source,scanned_destination,confirmed_quantity,destination_hu,device,idempotency_key)

@frappe.whitelist()
def raise_exception(task_name, exception_code, remarks=None, revised_quantity=None):
    return _raise_exception(task_name, exception_code, remarks, revised_quantity)

@frappe.whitelist()
def reverse_task(task_name, reason=None):
    return _reverse_task(task_name, reason)

@frappe.whitelist()
def list_exception_codes(task_type=None):
    filters = {"active": 1}
    if task_type:
        codes = frappe.get_all("Allowed Task Type", filters={"task_type": task_type}, pluck="parent")
        if not codes: return []
        filters["name"] = ["in", codes]
    return frappe.get_all("WMS Exception Code", filters=filters, fields=["name", "exception_name", "category", "requires_supervisor", "requires_comment"])

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
def repack(source_hu,destination_hu,items,idempotency_key,packing_order=None):
    reference_doctype = "Packing Order" if packing_order else "Handling Unit"
    reference_name = packing_order or source_hu
    return _repack(source_hu,destination_hu,parse_json(items,"items"),reference_doctype,reference_name,idempotency_key)

@frappe.whitelist()
def complete_packing_order(packing_order_name):
    return _complete_packing_order(packing_order_name)

@frappe.whitelist()
def list_open_packing_orders():
    return _list_open_packing_orders()

@frappe.whitelist()
def create_and_confirm_move(warehouse, product, quantity, stock_uom, stock_type, destination_bin, source_bin=None, source_hu=None, destination_hu=None, batch_no=None, serial_no=None, device=None):
    return _create_and_confirm_move(
        warehouse=warehouse, product=product, quantity=quantity, stock_uom=stock_uom, stock_type=stock_type,
        source_bin=source_bin, source_hu=source_hu, destination_bin=destination_bin, destination_hu=destination_hu,
        batch_no=batch_no, serial_no=serial_no, device=device,
    )
