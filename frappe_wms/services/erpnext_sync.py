import frappe
from frappe import _
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

def _cancel_doc(doctype, name):
    if not name: return
    doc = frappe.get_doc(doctype, name)
    if doc.docstatus == 1:
        doc.flags.ignore_permissions = True
        doc.cancel()

# --- Goods Receipt -> Stock Entry (no PO) or Purchase Receipt (PO-linked) ---

def _po_link_for_gr_row(row):
    if not row.inbound_delivery_item: return None, None
    return frappe.db.get_value("Inbound Delivery Item", row.inbound_delivery_item, ["purchase_order", "purchase_order_item"]) or (None, None)

def sync_goods_receipt(doc):
    if doc.get("erpnext_stock_entry") or doc.get("erpnext_purchase_receipt"): return
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return
    po_links = [_po_link_for_gr_row(row) for row in doc.items]
    linked = [l for l in po_links if l[0]]
    if linked and len(linked) != len(doc.items):
        frappe.throw(_("Goods Receipt {0} mixes Purchase-Order-linked and standalone lines; post them as separate receipts").format(doc.name))
    if linked:
        _sync_goods_receipt_to_purchase_receipt(doc, erpnext_warehouse, po_links)
    else:
        _sync_goods_receipt_to_stock_entry(doc, erpnext_warehouse)

def _sync_goods_receipt_to_stock_entry(doc, erpnext_warehouse):
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Receipt", company=company, remarks=f"frappe_wms Goods Receipt {doc.name}")
    for row in doc.items:
        _append_row(se, row, target_field="t_warehouse", erpnext_warehouse=erpnext_warehouse)
    se.flags.wms_managed_posting = True
    se.insert(ignore_permissions=True)
    se.submit()
    doc.db_set("erpnext_stock_entry", se.name, update_modified=False)

def _sync_goods_receipt_to_purchase_receipt(doc, erpnext_warehouse, po_links):
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    po_names = {link[0] for link in po_links}
    if len(po_names) != 1:
        frappe.throw(_("Goods Receipt {0} references more than one Purchase Order; post them separately").format(doc.name))
    qty_by_po_item = {}
    for row, (_po, po_item) in zip(doc.items, po_links):
        qty_by_po_item[po_item] = qty_by_po_item.get(po_item, 0) + flt(row.quantity)

    pr = make_purchase_receipt(po_names.pop())
    kept = []
    for item in pr.items:
        qty = qty_by_po_item.get(item.purchase_order_item)
        if not qty: continue
        item.qty = qty
        item.stock_qty = qty * flt(item.conversion_factor or 1)
        item.warehouse = erpnext_warehouse
        kept.append(item)
    if not kept: frappe.throw(_("No matching Purchase Order rows found for Goods Receipt {0}").format(doc.name))
    pr.items = kept
    pr.flags.ignore_permissions = True
    pr.flags.wms_managed_posting = True
    pr.insert(ignore_permissions=True)
    pr.submit()
    doc.db_set("erpnext_purchase_receipt", pr.name, update_modified=False)

def reverse_goods_receipt(doc):
    if doc.get("erpnext_purchase_receipt"):
        _cancel_doc("Purchase Receipt", doc.erpnext_purchase_receipt)
    else:
        _cancel_doc("Stock Entry", doc.get("erpnext_stock_entry"))

# --- Goods Issue -> Stock Entry (no SO) or Delivery Note (SO-linked) ---

def _so_link_for_gi_row(row):
    if not row.outbound_delivery_item: return None, None
    return frappe.db.get_value("Outbound Delivery Item", row.outbound_delivery_item, ["sales_order", "sales_order_item"]) or (None, None)

def sync_goods_issue(doc):
    if doc.get("erpnext_stock_entry") or doc.get("erpnext_delivery_note"): return
    erpnext_warehouse = _erpnext_warehouse(doc.warehouse)
    if not erpnext_warehouse: return
    so_links = [_so_link_for_gi_row(row) for row in doc.items]
    linked = [l for l in so_links if l[0]]
    if linked and len(linked) != len(doc.items):
        frappe.throw(_("Goods Issue {0} mixes Sales-Order-linked and standalone lines; post them as separate issues").format(doc.name))
    if linked:
        _sync_goods_issue_to_delivery_note(doc, erpnext_warehouse, so_links)
    else:
        _sync_goods_issue_to_stock_entry(doc, erpnext_warehouse)

def _sync_goods_issue_to_stock_entry(doc, erpnext_warehouse):
    company = frappe.db.get_value("WMS Warehouse", doc.warehouse, "company")
    se = _make_stock_entry(stock_entry_type="Material Issue", company=company, remarks=f"frappe_wms Goods Issue {doc.name}")
    for row in doc.items:
        _append_row(se, row, target_field="s_warehouse", erpnext_warehouse=erpnext_warehouse)
    se.flags.wms_managed_posting = True
    se.insert(ignore_permissions=True)
    se.submit()
    doc.db_set("erpnext_stock_entry", se.name, update_modified=False)

def _sync_goods_issue_to_delivery_note(doc, erpnext_warehouse, so_links):
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    so_names = {link[0] for link in so_links}
    if len(so_names) != 1:
        frappe.throw(_("Goods Issue {0} references more than one Sales Order; post them separately").format(doc.name))
    qty_by_so_item = {}
    for row, (_so, so_item) in zip(doc.items, so_links):
        qty_by_so_item[so_item] = qty_by_so_item.get(so_item, 0) + flt(row.quantity)

    dn = make_delivery_note(so_names.pop())
    kept = []
    for item in dn.items:
        qty = qty_by_so_item.get(item.so_detail)
        if not qty: continue
        item.qty = qty
        item.stock_qty = qty * flt(item.conversion_factor or 1)
        item.warehouse = erpnext_warehouse
        kept.append(item)
    if not kept: frappe.throw(_("No matching Sales Order rows found for Goods Issue {0}").format(doc.name))
    dn.items = kept
    dn.flags.ignore_permissions = True
    dn.flags.wms_managed_posting = True
    dn.insert(ignore_permissions=True)
    dn.submit()
    doc.db_set("erpnext_delivery_note", dn.name, update_modified=False)

def reverse_goods_issue(doc):
    if doc.get("erpnext_delivery_note"):
        _cancel_doc("Delivery Note", doc.erpnext_delivery_note)
    else:
        _cancel_doc("Stock Entry", doc.get("erpnext_stock_entry"))
