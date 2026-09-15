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
        outstanding = flt(row.qty) - flt(row.delivered_qty)
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
