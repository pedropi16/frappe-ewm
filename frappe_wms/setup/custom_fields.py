"""Fields the WMS adds to ERPNext's own documents (run after install and every migrate)."""
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    # The ECC-delivery pattern: a draft Delivery Note / Purchase Receipt that was replicated to
    # the warehouse points at the WMS delivery that executes it (services/erp_integration.py).
    "Delivery Note": [
        {"fieldname": "wms_stock_owner", "label": "WMS Owner", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "customer", "print_hide": 1},
        {"fieldname": "wms_entitled_party", "label": "WMS Party Entitled to Dispose", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "wms_stock_owner", "print_hide": 1},
        {"fieldname": "wms_outbound_delivery", "label": "WMS Outbound Delivery", "fieldtype": "Link", "options": "Outbound Delivery",
         "insert_after": "customer", "read_only": 1, "no_copy": 1, "allow_on_submit": 1, "print_hide": 1},
    ],
    # Allocation never picks stock expiring sooner than this for the customer (services/allocation.py);
    # the product's own minimum applies too, whichever is stricter.
    "Customer": [
        {"fieldname": "wms_minimum_remaining_shelf_life", "label": "WMS Minimum Remaining Shelf Life (Days)", "fieldtype": "Int",
         "insert_after": "customer_group", "non_negative": 1},
    ],
    "Purchase Order": [
        {"fieldname": "wms_stock_owner", "label": "WMS Owner", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "supplier", "print_hide": 1,
         "description": "Whose stock the warehouse receives (blank = the warehouse's own). Goes onto the WMS inbound delivery and the stock."},
        {"fieldname": "wms_entitled_party", "label": "WMS Party Entitled to Dispose", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "wms_stock_owner", "print_hide": 1},
    ],
    "Sales Order": [
        {"fieldname": "wms_stock_owner", "label": "WMS Owner", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "customer", "print_hide": 1,
         "description": "Whose stock is delivered (blank = the warehouse's own). The WMS outbound delivery only takes this owner's stock."},
        {"fieldname": "wms_entitled_party", "label": "WMS Party Entitled to Dispose", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "wms_stock_owner", "print_hide": 1},
    ],
    # MB1A: a draft Stock Entry (receipt, issue, transfer out of / into a WMS warehouse) is the goods movement the warehouse executes.
    "Stock Entry": [
        {"fieldname": "wms_stock_owner", "label": "WMS Owner", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "purpose", "print_hide": 1},
        {"fieldname": "wms_entitled_party", "label": "WMS Party Entitled to Dispose", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "wms_stock_owner", "print_hide": 1},
        {"fieldname": "wms_inbound_delivery", "label": "WMS Inbound Delivery", "fieldtype": "Link", "options": "Inbound Delivery",
         "insert_after": "purpose", "read_only": 1, "no_copy": 1, "allow_on_submit": 1, "print_hide": 1},
        {"fieldname": "wms_outbound_delivery", "label": "WMS Outbound Delivery", "fieldtype": "Link", "options": "Outbound Delivery",
         "insert_after": "wms_inbound_delivery", "read_only": 1, "no_copy": 1, "allow_on_submit": 1, "print_hide": 1},
    ],
    "Purchase Receipt": [
        {"fieldname": "wms_stock_owner", "label": "WMS Owner", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "supplier", "print_hide": 1},
        {"fieldname": "wms_entitled_party", "label": "WMS Party Entitled to Dispose", "fieldtype": "Link", "options": "WMS Stock Owner", "insert_after": "wms_stock_owner", "print_hide": 1},
        {"fieldname": "wms_inbound_delivery", "label": "WMS Inbound Delivery", "fieldtype": "Link", "options": "Inbound Delivery",
         "insert_after": "supplier", "read_only": 1, "no_copy": 1, "allow_on_submit": 1, "print_hide": 1},
    ],
}


def ensure_custom_fields():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
