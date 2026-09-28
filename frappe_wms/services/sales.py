import frappe
from frappe import _
from frappe.utils import flt, nowdate

def create_outbound_delivery_from_sales_order(sales_order_name, warehouse):
    so = frappe.get_doc("Sales Order", sales_order_name)
    if so.docstatus != 1: frappe.throw(_("Sales Order must be submitted"))
    wh = frappe.get_doc("WMS Warehouse", warehouse)
    if not wh.default_stock_type: frappe.throw(_("WMS Warehouse {0} has no default stock type configured").format(warehouse))

    items = []
    for row in so.items:
        # delivered_qty accumulates in the SO row's own transactional UOM (same as qty), but
        # the WMS side always tracks stock_uom - convert before handing it to the Outbound
        # Delivery, or a non-1 conversion_factor would silently under/over-state what's left.
        outstanding = (flt(row.qty) - flt(row.delivered_qty)) * flt(row.conversion_factor or 1)
        if outstanding <= 0: continue
        items.append({
            "line_number": len(items) + 1, "item": row.item_code, "requested_quantity": outstanding,
            "stock_uom": row.stock_uom, "required_stock_type": wh.default_stock_type,
            "sales_order": so.name, "sales_order_item": row.name,
        })
    if not items: frappe.throw(_("Sales Order {0} has no outstanding quantity to deliver").format(so.name))

    doc = frappe.get_doc({
        "doctype": "Outbound Delivery", "outbound_delivery_number": f"{so.name}-{frappe.generate_hash(length=4)}", "warehouse": warehouse,
        "customer": so.customer, "delivery_date": so.delivery_date or nowdate(), "staging_bin": wh.default_shipping_bin,
        "priority": "Normal", "items": items,
    })
    doc.insert(ignore_permissions=True)
    return doc.name

def _vendor_returns_customer():
    # Outbound Delivery's customer field is mandatory (it's normally an external-shipping
    # document), but a return to vendor has no customer at all - same fixed-placeholder idiom as
    # _customer_returns_supplier/_production_supplier in services/receipt.py. The real party (the
    # supplier) lives on the mirrored ERPNext return Purchase Receipt, derived from the original.
    name = "WMS Vendor Returns (Internal)"
    if not frappe.db.exists("Customer", name):
        frappe.get_doc({"doctype": "Customer", "customer_name": name, "customer_type": "Company"}).insert(ignore_permissions=True)
    return name

def create_return_outbound_delivery(purchase_receipt, warehouse, stock_type="DAMAGED"):
    # Return to vendor: stock ships out through the ordinary RF Ship flow
    # (create_and_submit_goods_issue) like any other outbound movement, sourced from whichever
    # stock type it actually landed in after inspection (DAMAGED by default - the common real
    # reason to send something back rather than scrap it) instead of the warehouse's normal
    # default. Carries source_document_type/number/line so the mirror
    # (erpnext_sync._sync_goods_issue_to_return_purchase_receipt) can build a proper return
    # Purchase Receipt against the original row-for-row.
    pr = frappe.get_doc("Purchase Receipt", purchase_receipt)
    if pr.docstatus != 1: frappe.throw(_("Purchase Receipt must be submitted before it can be returned"))
    wh = frappe.get_doc("WMS Warehouse", warehouse)
    if not wh.default_shipping_bin: frappe.throw(_("WMS Warehouse {0} has no default shipping bin configured").format(warehouse))
    items = [{
        "line_number": i, "item": row.item_code, "requested_quantity": row.qty, "stock_uom": row.stock_uom,
        "required_stock_type": stock_type, "source_document_type": "Purchase Receipt",
        "source_document_number": pr.name, "source_document_line": row.name,
    } for i, row in enumerate(pr.items, 1)]
    if not items: frappe.throw(_("Purchase Receipt {0} has no items to return").format(pr.name))
    doc = frappe.get_doc({
        "doctype": "Outbound Delivery", "outbound_delivery_number": f"{pr.name}-RET-{frappe.generate_hash(length=4)}",
        "warehouse": warehouse, "customer": _vendor_returns_customer(), "delivery_date": nowdate(),
        "staging_bin": wh.default_shipping_bin, "priority": "Normal", "items": items,
    })
    doc.insert(ignore_permissions=True)
    return doc.name
