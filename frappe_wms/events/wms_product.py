import frappe
from frappe import _

def validate_product(doc, method=None):
    item = frappe.get_cached_doc("Item", doc.item)
    if doc.warehouse_managed and not item.is_stock_item:
        frappe.throw(_("Item {0} is not a stock item in ERPNext and cannot be warehouse managed").format(doc.item))
    if not doc.stock_uom:
        doc.stock_uom = item.stock_uom
    elif doc.stock_uom != item.stock_uom:
        frappe.throw(_("Stock UOM {0} does not match the ERPNext Item's stock UOM {1}").format(doc.stock_uom, item.stock_uom))
    groups = {frappe.db.get_value("Handling Indicator", r.indicator, "required_storage_group") for r in doc.get("handling_indicators") or []} - {None, ""}
    if len(groups) > 1:
        frappe.throw(_("The handling indicators of {0} require different storage groups ({1}); a bin is in only one").format(doc.item, ", ".join(sorted(groups))))
