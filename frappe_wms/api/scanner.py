import frappe
from frappe import _
from frappe_wms.services.task import confirm_task as _confirm_task, list_my_tasks as _list_my_tasks, raise_exception as _raise_exception, reverse_task as _reverse_task, create_and_confirm_move as _create_and_confirm_move
from frappe_wms.services.packing import repack as _repack, repack_loose as _repack_loose, complete_packing_order as _complete_packing_order, list_open_packing_orders as _list_open_packing_orders
from frappe_wms.utils import parse_json, require_role
from frappe_wms.services.resource import RESOURCE_ROLES as RF_ROLES
from frappe_wms.services.idempotency import run_once

@frappe.whitelist()
def get_task(task_name):
    doc=frappe.get_doc("Warehouse Task",task_name); doc.check_permission("read")
    result = doc.as_dict()
    if len(doc.stock_allocations or []) > 1:
        # A cluster pick task's own fields only show the aggregate - the RF app needs the
        # per-order breakdown (which delivery gets how much) so an operator sorting the pick
        # into multiple totes/orders knows the split, not just the combined total.
        deliveries = frappe.get_all("Stock Allocation", filters={"name": ["in", [r.stock_allocation for r in doc.stock_allocations]]}, fields=["name", "outbound_delivery"])
        delivery_by_allocation = {r.name: r.outbound_delivery for r in deliveries}
        numbers = {r.name: r.outbound_delivery_number for r in frappe.get_all("Outbound Delivery", filters={"name": ["in", list(delivery_by_allocation.values())]}, fields=["name", "outbound_delivery_number"])}
        for row in result["stock_allocations"]:
            delivery = delivery_by_allocation.get(row["stock_allocation"])
            row["outbound_delivery"] = delivery
            row["outbound_delivery_number"] = numbers.get(delivery)
    return result

@frappe.whitelist()
def my_tasks():
    result = _list_my_tasks()
    # The RF app needs this up front to know whether to add a product-scan step to the confirm flow.
    result["settings"] = {"require_scan_verification": int(frappe.db.get_single_value("WMS Settings", "require_scan_verification") or 0)}
    return result

@frappe.whitelist()
def confirm_task(task_name, scanned_source=None, scanned_destination=None, confirmed_quantity=None, destination_hu=None, device=None, idempotency_key=None, scanned_product=None):
    return _confirm_task(task_name,scanned_source,scanned_destination,confirmed_quantity,destination_hu,device,idempotency_key,scanned_product)

@frappe.whitelist()
def raise_exception(task_name, exception_code, remarks=None, revised_quantity=None):
    return _raise_exception(task_name, exception_code, remarks, revised_quantity)

@frappe.whitelist()
def reverse_task(task_name, reason=None):
    return _reverse_task(task_name, reason)

@frappe.whitelist()
def list_exception_codes(task_type=None):
    require_role(*RF_ROLES)
    filters = {"active": 1}
    if task_type:
        codes = frappe.get_all("Allowed Task Type", filters={"task_type": task_type}, pluck="parent")
        if not codes: return []
        filters["name"] = ["in", codes]
    return frappe.get_all("WMS Exception Code", filters=filters, fields=["name", "exception_name", "category", "requires_supervisor", "requires_comment", "allows_quantity_change"])

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
    handling_units=frappe.get_all("Handling Unit",filters={"current_bin":bin_doc.name,"parent_hu":["is","not set"]},fields=["name","hu_type","status","stock_status"])
    return {"storage_bin":bin_doc.as_dict(),"stock":stock,"handling_units":handling_units}

@frappe.whitelist()
def repack(source_hu,destination_hu,items,idempotency_key,packing_order=None):
    reference_doctype = "Packing Order" if packing_order else "Handling Unit"
    reference_name = packing_order or source_hu
    return _repack(source_hu,destination_hu,parse_json(items,"items"),reference_doctype,reference_name,idempotency_key)

@frappe.whitelist()
def repack_loose(storage_bin,items,idempotency_key,source_hu=None,destination_hu=None):
    reference_name = destination_hu or source_hu or storage_bin
    return _repack_loose(storage_bin,source_hu,destination_hu,parse_json(items,"items"),"Handling Unit",reference_name,idempotency_key)

@frappe.whitelist()
def complete_packing_order(packing_order_name):
    return _complete_packing_order(packing_order_name)

@frappe.whitelist()
def list_open_packing_orders():
    return _list_open_packing_orders()

@frappe.whitelist()
def create_and_confirm_move(warehouse, product, quantity, stock_uom, stock_type, destination_bin, source_bin=None, source_hu=None, destination_hu=None, batch_no=None, serial_no=None, device=None, idempotency_key=None):
    return run_once(idempotency_key, lambda: _create_and_confirm_move(
        warehouse=warehouse, product=product, quantity=quantity, stock_uom=stock_uom, stock_type=stock_type,
        source_bin=source_bin, source_hu=source_hu, destination_bin=destination_bin, destination_hu=destination_hu,
        batch_no=batch_no, serial_no=serial_no, device=device,
    ))

@frappe.whitelist()
def resolve_scan(code, warehouse=None):
    """Classify a scanned code as a Storage Bin, Handling Unit and/or Item so the RF app can tell an operator "that is a
    bin, not a product" instead of failing later, and can turn an item barcode (EAN/UPC) into the item code the task
    verification compares against. Returns every match; the caller decides which kind it expects.

    Gated on the RF roles, not on doctype read permission: an operator confirming a task may not be allowed to open the
    Item form, but still has to be able to scan its barcode. Only existence, name, and unit of measure are returned."""
    require_role(*RF_ROLES)
    code = (code or "").strip()
    matches = []
    if not code: return {"code": code, "matches": matches}
    if frappe.db.exists("Storage Bin", code):
        matches.append({"type": "bin", "name": code, "warehouse": frappe.db.get_value("Storage Bin", code, "warehouse")})
    if frappe.db.exists("Handling Unit", code):
        matches.append({"type": "hu", "name": code})
    item = code if frappe.db.exists("Item", code) else frappe.db.get_value("Item Barcode", {"barcode": code}, "parent")
    if item:
        matches.append({"type": "item", "name": item, "item_name": frappe.db.get_value("Item", item, "item_name"), "stock_uom": frappe.db.get_value("Item", item, "stock_uom")})
    return {"code": code, "matches": matches}
