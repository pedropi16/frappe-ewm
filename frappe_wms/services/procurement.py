import frappe
from frappe import _
from frappe.utils import flt, nowdate

def create_inbound_delivery_from_purchase_order(purchase_order_name, warehouse):
    # The manual "Create > Inbound Delivery" button - same builder as automatic replication
    # (services/erp_integration.py), see sales.create_outbound_delivery_from_sales_order.
    from frappe_wms.services.erp_integration import create_deliveries_for_order
    po = frappe.get_doc("Purchase Order", purchase_order_name)
    if po.docstatus != 1: frappe.throw(_("Purchase Order must be submitted"))
    wh = frappe.get_doc("WMS Warehouse", warehouse)
    if not wh.default_receiving_bin: frappe.throw(_("WMS Warehouse {0} has no default receiving bin configured").format(warehouse))
    if not wh.default_stock_type: frappe.throw(_("WMS Warehouse {0} has no default stock type configured").format(warehouse))
    names = create_deliveries_for_order(po, wms_warehouse=warehouse, trigger_only=False, release=False)
    if not names: frappe.throw(_("Purchase Order {0} has no outstanding quantity to receive").format(po.name))
    return names[0]
