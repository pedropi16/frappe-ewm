import frappe
from frappe import _
from frappe.utils import flt, nowdate

def create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse):
    po = frappe.get_doc("Purchase Order", purchase_order_name)
    if po.docstatus != 1: frappe.throw(_("Purchase Order must be submitted"))
    wh = frappe.get_doc("WMS Warehouse", warehouse)
    if not wh.default_receiving_bin: frappe.throw(_("WMS Warehouse {0} has no default receiving bin configured").format(warehouse))
    if not wh.default_stock_type: frappe.throw(_("WMS Warehouse {0} has no default stock type configured").format(warehouse))

    items = []
    for row in po.items:
        # received_qty accumulates in the PO row's own transactional UOM (same as qty), but
        # the WMS side always tracks stock_uom - convert before handing it to the Inbound
        # Delivery, or a non-1 conversion_factor would silently under/over-state what's left.
        outstanding = (flt(row.qty) - flt(row.received_qty)) * flt(row.conversion_factor or 1)
        if outstanding <= 0: continue
        items.append({
            "line_number": len(items) + 1, "item": row.item_code, "expected_quantity": outstanding,
            "stock_uom": row.stock_uom, "expected_stock_type": wh.default_stock_type,
            "purchase_order": po.name, "purchase_order_item": row.name,
        })
    if not items: frappe.throw(_("Purchase Order {0} has no outstanding quantity to receive").format(po.name))

    doc = frappe.get_doc({
        "doctype": "Inbound Delivery", "inbound_delivery_number": f"{po.name}-{frappe.generate_hash(length=4)}", "warehouse": warehouse,
        "supplier": po.supplier, "company": po.company, "receiving_bin": wh.default_receiving_bin,
        "posting_date": nowdate(), "items": items,
    })
    doc.insert(ignore_permissions=True)
    return doc.name
