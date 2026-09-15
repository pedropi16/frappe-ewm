import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.utils import require_role

def snapshot_count(count_name):
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    if doc.status != "Draft": frappe.throw(_("Count has already been started"))
    filters = {"warehouse": doc.warehouse, "quantity": [">", 0]}
    if doc.storage_bin: filters["storage_bin"] = doc.storage_bin
    if doc.product: filters["product"] = doc.product
    balances = frappe.get_all("WMS Stock Balance", filters=filters, fields=["*"])
    if doc.storage_type:
        bins_in_type = set(frappe.get_all("Storage Bin", filters={"warehouse": doc.warehouse, "storage_type": doc.storage_type}, pluck="name"))
        balances = [b for b in balances if b.storage_bin in bins_in_type]
    if not balances: frappe.throw(_("No stock found for the given count scope"))
    doc.items = []
    for balance in balances:
        doc.append("items", {
            "product": balance.product, "batch_no": balance.batch_no, "serial_no": balance.serial_no,
            "handling_unit": balance.handling_unit, "storage_bin": balance.storage_bin, "stock_type": balance.stock_type,
            "stock_uom": balance.stock_uom, "book_quantity": balance.quantity, "status": "Open",
        })
    doc.status = "Counting"
    doc.counted_by = frappe.session.user
    doc.counted_at = now_datetime()
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "items": len(doc.items)}

def record_counts(count_name, counted_quantities):
    # counted_quantities: {row_name: counted_quantity}
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    if doc.status not in {"Counting", "Counted"}: frappe.throw(_("Count is not open for recording"))
    for row in doc.items:
        if row.name not in counted_quantities: continue
        row.counted_quantity = flt(counted_quantities[row.name])
        row.variance = row.counted_quantity - flt(row.book_quantity)
        row.status = "Counted"
    doc.status = "Counted" if all(r.status == "Counted" for r in doc.items) else "Counting"
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": doc.status}

def post_count(count_name):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    frappe.db.sql("select name from `tabWMS Physical Inventory Count` where name=%s for update", count_name)
    doc = frappe.get_doc("WMS Physical Inventory Count", count_name)
    if doc.status != "Counted": frappe.throw(_("All lines must be counted before posting"))
    for i, row in enumerate(doc.items, 1):
        if row.status == "Posted": continue
        variance = flt(row.variance)
        if variance != 0:
            movement_type = "701" if variance > 0 else "702"
            entry = {
                "warehouse": doc.warehouse, "product": row.product, "batch_no": row.batch_no, "serial_no": row.serial_no,
                "handling_unit": row.handling_unit, "storage_bin": row.storage_bin, "stock_type": row.stock_type,
                "quantity": variance, "stock_uom": row.stock_uom, "movement_type": movement_type, "reference_line": row.name,
            }
            post_entries([entry], doc.doctype, doc.name, f"PIC:{doc.name}:{i}")
        row.status = "Posted"
    doc.status = "Posted"
    doc.posted_by = frappe.session.user
    doc.posted_at = now_datetime()
    doc.save(ignore_permissions=True)
    return {"count": doc.name, "status": "Posted"}
