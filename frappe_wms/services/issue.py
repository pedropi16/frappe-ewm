import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.stock import post_entries
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

READY_TO_SHIP_STATUSES = ("Picking", "Picked", "Packing", "Packed", "Staging", "Staged")

def post_goods_issue(doc):
    if frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype":doc.doctype,"reference_name":doc.name}): return
    for i,row in enumerate(doc.items,1):
        hu=frappe.get_doc("Handling Unit",row.handling_unit)
        if hu.status not in {"Loaded","Staged"}: frappe.throw(_("HU {0} is not staged or loaded").format(hu.name))
        entry={"warehouse":doc.warehouse,"product":row.item,"batch_no":row.batch_no,"serial_no":row.serial_no,"handling_unit":row.handling_unit,"storage_bin":doc.staging_bin,"stock_type":row.stock_type,"quantity":-row.quantity,"stock_uom":row.stock_uom,"movement_type":"601","reference_line":row.name}
        post_entries([entry],doc.doctype,doc.name,f"GI:{doc.name}:{i}")
        hu.flags.wms_service_update=True; hu.status="Shipped"; hu.save(ignore_permissions=True)
        if row.outbound_delivery_item:
            current = flt(frappe.db.get_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity"))
            frappe.db.set_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity", current + flt(row.quantity))
    doc.db_set("status","Posted")
    _update_delivery_issue_status(doc.outbound_delivery)

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
    for row in doc.items:
        if row.outbound_delivery_item:
            current = flt(frappe.db.get_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity"))
            frappe.db.set_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity", max(current - flt(row.quantity), 0))
    doc.db_set({"status":"Reversed","reversed":1})
    _update_delivery_issue_status(doc.outbound_delivery)

def _update_delivery_issue_status(delivery_name):
    if not delivery_name: return
    rows = frappe.get_all("Outbound Delivery Item", filters={"parent": delivery_name}, fields=["requested_quantity", "issued_quantity"])
    if not rows: return
    fully_issued = all(flt(r.issued_quantity) >= flt(r.requested_quantity) for r in rows)
    any_issued = any(flt(r.issued_quantity) > 0 for r in rows)
    goods_issue_status = "Posted" if fully_issued else ("Partially Posted" if any_issued else "Not Posted")
    # Keep status in sync in both directions: a reversal that drops a delivery below fully
    # issued must not leave it stuck on "Goods Issued".
    values = {"goods_issue_status": goods_issue_status, "status": "Goods Issued" if fully_issued else "Staged"}
    frappe.db.set_value("Outbound Delivery", delivery_name, values)

def list_ready_to_ship(user=None):
    resource = my_resource(user)
    filters = {"picking_status": "Picked", "goods_issue_status": ["!=", "Posted"], "status": ["in", READY_TO_SHIP_STATUSES]}
    if resource: filters["warehouse"] = resource.warehouse
    deliveries = frappe.get_list("Outbound Delivery", filters=filters,
        fields=["name", "outbound_delivery_number", "warehouse", "customer", "staging_bin", "route", "door", "status", "delivery_date"],
        order_by="delivery_date asc, creation asc", limit=50)
    for delivery in deliveries:
        rows = frappe.get_all("Outbound Delivery Item", filters={"parent": delivery.name}, fields=["name", "item", "picked_quantity", "issued_quantity", "stock_uom", "required_stock_type"])
        for row in rows:
            row["remaining_quantity"] = flt(row.picked_quantity) - flt(row.issued_quantity)
            allocation = frappe.get_all("Stock Allocation", filters={"outbound_delivery_item": row.name, "status": ["in", ["Picked", "Partially Picked"]]}, fields=["handling_unit"], limit=1)
            row["suggested_handling_unit"] = allocation[0].handling_unit if allocation else None
        delivery["items"] = [r for r in rows if r["remaining_quantity"] > 0]
    return [d for d in deliveries if d["items"]]

def create_and_submit_goods_issue(outbound_delivery, items):
    # items: [{outbound_delivery_item, item, quantity, stock_uom, handling_unit, stock_type}]
    require_role("WMS Operator", "WMS Loader", "WMS Supervisor")
    delivery = frappe.get_doc("Outbound Delivery", outbound_delivery)
    if not items: frappe.throw(_("At least one issue line is required"))
    gi = frappe.get_doc({
        "doctype": "Goods Issue", "outbound_delivery": delivery.name, "warehouse": delivery.warehouse,
        "staging_bin": delivery.staging_bin, "items": items,
    })
    gi.insert(ignore_permissions=True)
    gi.flags.ignore_permissions = True
    gi.submit()
    return {"goods_issue": gi.name}
