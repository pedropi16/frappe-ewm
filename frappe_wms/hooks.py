app_name = "frappe_wms"
app_title = "Frappe WMS"
app_publisher = "Pedro Pino Otero"
app_description = "Warehouse Management and Execution System"
app_email = ""
app_license = "MIT"
app_version = "0.1.0"

required_apps = ["erpnext"]

app_include_js = ["/assets/frappe_wms/js/frappe_wms.js"]
app_include_css = ["/assets/frappe_wms/css/frappe_wms.css"]

after_install = "frappe_wms.install.after_install"

doc_events = {
    "Handling Unit": {
        "validate": "frappe_wms.events.handling_unit.validate_hu",
        "on_update": "frappe_wms.events.handling_unit.on_hu_update",
    },
    "Storage Bin": {"validate": "frappe_wms.events.storage_bin.validate_storage_bin"},
    "WMS Product": {"validate": "frappe_wms.events.wms_product.validate_product"},
    "Warehouse Task": {
        "validate": "frappe_wms.events.warehouse_task.validate_task",
        "on_cancel": "frappe_wms.events.warehouse_task.prevent_direct_cancel_after_posting",
    },
    "Inbound Delivery": {"validate": "frappe_wms.events.deliveries.validate_inbound_delivery"},
    "Outbound Delivery": {"validate": "frappe_wms.events.deliveries.validate_outbound_delivery"},
    "Goods Receipt": {
        "on_submit": "frappe_wms.events.goods_receipt.on_submit",
        "on_cancel": "frappe_wms.events.goods_receipt.on_cancel",
    },
    "Goods Issue": {
        "on_submit": "frappe_wms.events.goods_issue.on_submit",
        "on_cancel": "frappe_wms.events.goods_issue.on_cancel",
    },
}

scheduler_events = {
    "hourly": ["frappe_wms.tasks.recalculate_stale_bin_capacity"],
    "daily": [
        "frappe_wms.tasks.verify_stock_balance_integrity",
        "frappe_wms.tasks.verify_erpnext_stock_reconciliation",
    ],
}

permission_query_conditions = {
    "WMS Stock Ledger Entry": "frappe_wms.permissions.ledger_query",
    "Warehouse Task": "frappe_wms.permissions.task_query",
}

has_permission = {
    "WMS Stock Ledger Entry": "frappe_wms.permissions.ledger_has_permission",
    "WMS Stock Balance": "frappe_wms.permissions.balance_has_permission",
}
