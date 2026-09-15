import frappe
from frappe.utils import flt

def _erpnext_warehouse(wms_warehouse):
    return frappe.db.get_value("WMS Warehouse", wms_warehouse, "erpnext_warehouse")

def _make_stock_entry(*, stock_entry_type, company, remarks):
    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = stock_entry_type
    se.company = company
    se.remarks = remarks
    return se

def _append_row(se, row, *, target_field, erpnext_warehouse):
    se.append("items", {
        "item_code": row.item,
        "qty": flt(row.quantity),
        "uom": row.stock_uom,
        "stock_uom": row.stock_uom,
        "conversion_factor": 1,
        "batch_no": row.batch_no,
        "serial_no": row.serial_no,
        "use_serial_batch_fields": 1,
        "allow_zero_valuation_rate": 1,
        target_field: erpnext_warehouse,
    })

def sync_goods_receipt(doc):
    if doc.get("erpnext_stock_entry"): return
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Receipt", company=company, remarks=f"frappe_wms Goods Receipt {doc.name}")
    for row in doc.items:
        _append_row(se, row, target_field="t_warehouse", erpnext_warehouse=erpnext_warehouse)
    se.insert(ignore_permissions=True)
    se.submit()
    doc.db_set("erpnext_stock_entry", se.name, update_modified=False)

def reverse_goods_receipt(doc):
    _cancel_stock_entry(doc.get("erpnext_stock_entry"))

def sync_goods_issue(doc):
    if doc.get("erpnext_stock_entry"): return
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Issue", company=company, remarks=f"frappe_wms Goods Issue {doc.name}")
    for row in doc.items:
        _append_row(se, row, target_field="s_warehouse", erpnext_warehouse=erpnext_warehouse)
    se.insert(ignore_permissions=True)
    se.submit()
    doc.db_set("erpnext_stock_entry", se.name, update_modified=False)

def reverse_goods_issue(doc):
    _cancel_stock_entry(doc.get("erpnext_stock_entry"))

def _cancel_stock_entry(name):
    if not name: return
    se = frappe.get_doc("Stock Entry", name)
    if se.docstatus == 1:
        se.flags.ignore_permissions = True
        se.cancel()
