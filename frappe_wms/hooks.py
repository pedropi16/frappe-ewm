app_name = "frappe_wms"
app_title = "Frappe WMS"
app_publisher = "Pedro Pino Otero"
app_description = "Warehouse Management and Execution System"
app_email = ""
app_license = "MIT"
app_version = "0.1.0"
app_logo_url = "/assets/frappe_wms/images/wms-logo.svg"
app_home = "/app/wms"

required_apps = ["erpnext"]

add_to_apps_screen = [
    {
        "name": "frappe_wms",
        "logo": app_logo_url,
        "title": app_title,
        "route": app_home,
        "has_permission": "frappe_wms.permissions.has_app_permission",
    }
]

app_include_js = ["/assets/frappe_wms/js/frappe_wms.js"]
app_include_css = ["/assets/frappe_wms/css/frappe_wms.css"]

doctype_js = {
    "Purchase Order": "public/js/purchase_order.js",
    "Sales Order": "public/js/sales_order.js",
}

after_install = "frappe_wms.install.after_install"

_NUMBER_RANGE_AUTONAME = "frappe_wms.services.numbering.autoname_from_range"

doc_events = {
    "Handling Unit": {
        "validate": "frappe_wms.events.handling_unit.validate_hu",
        "on_update": "frappe_wms.events.handling_unit.on_hu_update",
    },
    "Storage Bin": {"validate": "frappe_wms.events.storage_bin.validate_storage_bin"},
    "WMS Product": {"validate": "frappe_wms.events.wms_product.validate_product"},
    "Warehouse Order": {"autoname": _NUMBER_RANGE_AUTONAME},
    "Warehouse Task": {
        "autoname": _NUMBER_RANGE_AUTONAME,
        "validate": "frappe_wms.events.warehouse_task.validate_task",
        "on_cancel": "frappe_wms.events.warehouse_task.prevent_direct_cancel_after_posting",
    },
    "Warehouse Request": {"autoname": _NUMBER_RANGE_AUTONAME},
    "Inbound Delivery": {
        "autoname": _NUMBER_RANGE_AUTONAME,
        "validate": "frappe_wms.events.deliveries.validate_inbound_delivery",
    },
    "Outbound Delivery": {
        "autoname": _NUMBER_RANGE_AUTONAME,
        "validate": "frappe_wms.events.deliveries.validate_outbound_delivery",
        "before_cancel": "frappe_wms.events.deliveries.before_cancel_outbound_delivery",
        "on_cancel": "frappe_wms.events.deliveries.on_cancel_outbound_delivery",
    },
    "WMS Shipment": {"validate": "frappe_wms.events.shipment.validate_shipment"},
    "Goods Receipt": {
        "autoname": _NUMBER_RANGE_AUTONAME,
        "on_submit": "frappe_wms.events.goods_receipt.on_submit",
        "on_cancel": "frappe_wms.events.goods_receipt.on_cancel",
    },
    "Goods Issue": {
        "autoname": _NUMBER_RANGE_AUTONAME,
        "on_submit": "frappe_wms.events.goods_issue.on_submit",
        "on_cancel": "frappe_wms.events.goods_issue.on_cancel",
    },
    "Packing Order": {"autoname": _NUMBER_RANGE_AUTONAME},
    "VAS Order": {"autoname": _NUMBER_RANGE_AUTONAME},
    "WMS Wave": {"autoname": _NUMBER_RANGE_AUTONAME},
    "WMS Physical Inventory Count": {"autoname": _NUMBER_RANGE_AUTONAME},
    "WMS Quality Inspection": {"autoname": _NUMBER_RANGE_AUTONAME},
    "Stock Entry": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Delivery Note": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Purchase Receipt": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Stock Reconciliation": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Sales Invoice": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Purchase Invoice": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Subcontracting Receipt": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Subcontracting Order": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Work Order": {
        "validate": "frappe_wms.events.erpnext_stock_guard.validate",
        "on_submit": "frappe_wms.events.work_order.on_submit",
    },
    "Job Card": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
}

scheduler_events = {
    "hourly": [
        "frappe_wms.tasks.recalculate_stale_bin_capacity",
        "frappe_wms.tasks.run_replenishment_check",
    ],
    "daily": [
        "frappe_wms.tasks.verify_stock_balance_integrity",
        "frappe_wms.tasks.verify_erpnext_stock_reconciliation",
    ],
}

# Removal Rule strategy registry - other apps can add their own entries to this same hook
# name in their own hooks.py; frappe.get_hooks() merges every installed app's dict together.
wms_removal_strategies = {
    "FIFO": "frappe_wms.services.removal_rules.strategy_fifo",
    "LIFO": "frappe_wms.services.removal_rules.strategy_lifo",
    "FEFO": "frappe_wms.services.removal_rules.strategy_fefo",
    "Stringent FIFO": "frappe_wms.services.removal_rules.strategy_stringent_fifo",
    "Partial Quantity First": "frappe_wms.services.removal_rules.strategy_partial_quantity_first",
    "By Quantity": "frappe_wms.services.removal_rules.strategy_by_quantity",
    "Fixed Bin": "frappe_wms.services.removal_rules.strategy_fixed_bin",
}

permission_query_conditions = {
    "WMS Stock Ledger Entry": "frappe_wms.permissions.ledger_query",
    "Warehouse Task": "frappe_wms.permissions.task_query",
}

has_permission = {
    "WMS Stock Ledger Entry": "frappe_wms.permissions.ledger_has_permission",
    "WMS Stock Balance": "frappe_wms.permissions.balance_has_permission",
}
