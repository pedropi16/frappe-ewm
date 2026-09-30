"""Fields the WMS adds to ERPNext's own documents (run after install and every migrate)."""
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
    # The ECC-delivery pattern: a draft Delivery Note / Purchase Receipt that was replicated to
    # the warehouse points at the WMS delivery that executes it (services/erp_integration.py).
    "Delivery Note": [
        {"fieldname": "wms_outbound_delivery", "label": "WMS Outbound Delivery", "fieldtype": "Link", "options": "Outbound Delivery",
         "insert_after": "customer", "read_only": 1, "no_copy": 1, "allow_on_submit": 1, "print_hide": 1},
    ],
    "Purchase Receipt": [
        {"fieldname": "wms_inbound_delivery", "label": "WMS Inbound Delivery", "fieldtype": "Link", "options": "Inbound Delivery",
         "insert_after": "supplier", "read_only": 1, "no_copy": 1, "allow_on_submit": 1, "print_hide": 1},
    ],
}


def ensure_custom_fields():
    create_custom_fields(CUSTOM_FIELDS, ignore_validate=True, update=True)
