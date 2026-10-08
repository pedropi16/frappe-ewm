app_name = "frappe_wms"
app_title = "WMS"
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

app_include_js = ["/assets/frappe_wms/js/frappe_wms.js", "/assets/frappe_wms/js/erp_wms_status.js", "/assets/frappe_wms/js/wms_document_type.js", "/assets/frappe_wms/js/wms_delivery_form.js"]
app_include_css = ["/assets/frappe_wms/css/frappe_wms.css"]

doctype_js = {
    "Purchase Order": "public/js/purchase_order.js",
    "Sales Order": "public/js/sales_order.js",
    "Work Order": "public/js/work_order.js",
}

after_install = "frappe_wms.install.after_install"
after_migrate = ["frappe_wms.db_maintenance.ensure_indexes", "frappe_wms.setup.custom_fields.ensure_custom_fields",
                 "frappe_wms.setup.role_permissions.ensure_role_permissions"]
before_tests = "frappe_wms.tests.bootstrap.before_tests"
jinja = {"methods": ["frappe_wms.services.printing.packing_list_data", "frappe_wms.services.labeling.bin_qr_data_uri"]}

_NUMBER_RANGE_AUTONAME = "frappe_wms.services.numbering.autoname_from_range"

doc_events = {
    "*": {
        "after_insert": "frappe_wms.services.ppf.on_event",
        "on_update": "frappe_wms.services.ppf.on_event",
        "on_submit": "frappe_wms.services.ppf.on_event",
        "on_cancel": "frappe_wms.services.ppf.on_event",
    },
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
        "after_insert": "frappe_wms.events.warehouse_task.update_order_distance",
        "on_cancel": "frappe_wms.events.warehouse_task.prevent_direct_cancel_after_posting",
    },
    "Warehouse Request": {"autoname": _NUMBER_RANGE_AUTONAME, "validate": "frappe_wms.services.document_types.validate_document_type"},
    "WMS Delivery Request": {"autoname": _NUMBER_RANGE_AUTONAME, "validate": "frappe_wms.services.document_types.validate_document_type"},
    "Final Outbound Delivery": {"autoname": _NUMBER_RANGE_AUTONAME, "validate": "frappe_wms.services.document_types.validate_document_type"},
    "Inbound Delivery": {
        "autoname": _NUMBER_RANGE_AUTONAME,
        "validate": ["frappe_wms.events.deliveries.validate_inbound_delivery", "frappe_wms.services.document_types.validate_document_type"],
        "before_cancel": "frappe_wms.events.deliveries.before_cancel_inbound_delivery",
        "on_cancel": "frappe_wms.events.deliveries.on_cancel_inbound_delivery",
    },
    "Outbound Delivery": {
        "autoname": _NUMBER_RANGE_AUTONAME,
        "validate": ["frappe_wms.events.deliveries.validate_outbound_delivery", "frappe_wms.services.document_types.validate_document_type"],
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
    "WMS Opening Stock Load": {"autoname": _NUMBER_RANGE_AUTONAME},
    "WMS Posting Change": {"autoname": _NUMBER_RANGE_AUTONAME},
    "Stock Entry": {"validate": "frappe_wms.events.erpnext_stock_guard.validate", "before_cancel": "frappe_wms.events.erpnext_stock_guard.before_cancel",
        "on_update": "frappe_wms.services.erp_integration.on_stock_entry_update", "before_submit": "frappe_wms.services.erp_integration.before_draft_document_submit",
        "on_trash": "frappe_wms.services.erp_integration.on_order_cancel",
        "on_submit": "frappe_wms.services.production_supply.consume_from_stock_entry", "on_cancel": "frappe_wms.services.production_supply.reverse_consumption"},
    "Delivery Note": {
        "validate": "frappe_wms.events.erpnext_stock_guard.validate",
        "on_update": "frappe_wms.services.erp_integration.on_draft_document_update",
        "before_submit": "frappe_wms.services.erp_integration.before_draft_document_submit",
        "on_trash": "frappe_wms.services.erp_integration.on_order_cancel",
        "before_cancel": "frappe_wms.events.erpnext_stock_guard.before_cancel",
    },
    "Purchase Receipt": {
        "validate": "frappe_wms.events.erpnext_stock_guard.validate",
        "on_update": "frappe_wms.services.erp_integration.on_draft_document_update",
        "before_submit": "frappe_wms.services.erp_integration.before_draft_document_submit",
        "on_trash": "frappe_wms.services.erp_integration.on_order_cancel",
        "before_cancel": "frappe_wms.events.erpnext_stock_guard.before_cancel",
    },
    "Stock Reconciliation": {"validate": "frappe_wms.events.erpnext_stock_guard.validate", "before_cancel": "frappe_wms.events.erpnext_stock_guard.before_cancel"},
    "Sales Invoice": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Purchase Invoice": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Subcontracting Receipt": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Subcontracting Order": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Work Order": {
        "validate": "frappe_wms.events.erpnext_stock_guard.validate",
        "on_submit": "frappe_wms.events.work_order.on_submit",
        "on_cancel": "frappe_wms.events.work_order.on_cancel",
    },
    "Job Card": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "POS Invoice": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Asset Capitalization": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    "Asset Repair": {"validate": "frappe_wms.events.erpnext_stock_guard.validate"},
    # ERPNext -> WMS replication and change propagation (WMS Warehouse "ERP Integration").
    "Sales Order": {
        "on_submit": "frappe_wms.services.erp_integration.on_order_submit",
        "before_cancel": "frappe_wms.services.erp_integration.on_order_cancel",
        "on_update_after_submit": "frappe_wms.services.erp_integration.on_order_update_after_submit",
    },
    "Purchase Order": {
        "on_submit": "frappe_wms.services.erp_integration.on_order_submit",
        "before_cancel": "frappe_wms.services.erp_integration.on_order_cancel",
        "on_update_after_submit": "frappe_wms.services.erp_integration.on_order_update_after_submit",
    },
}

scheduler_events = {
    # ERPNext postings queued by warehouses in "Queued with Retry" mode (services/erp_sync_queue.py).
    "cron": {"*/10 * * * *": ["frappe_wms.services.erp_sync_queue.retry_due", "frappe_wms.services.printing.retry_print_jobs", "frappe_wms.services.ppf.run_scheduled_actions", "frappe_wms.services.mfs.retry_failed"]},
    "hourly": [
        "frappe_wms.tasks.recalculate_stale_bin_capacity",
        "frappe_wms.tasks.run_replenishment_check",
        "frappe_wms.tasks.generate_scheduled_waves",
        "frappe_wms.tasks.release_due_waves",
        "frappe_wms.services.production_supply.run_auto_staging",
        "frappe_wms.services.alerts.send_alert_digest",
        "frappe_wms.services.yard.mark_no_shows",
    ],
    "monthly": ["frappe_wms.services.archiving.monthly_archive"],
    "daily": [
        "frappe_wms.tasks.verify_stock_balance_integrity",
        "frappe_wms.tasks.verify_erpnext_stock_reconciliation",
        "frappe_wms.tasks.generate_scheduled_counts",
        "frappe_wms.services.storage_billing.snapshot_storage_usage",
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

# Further extension points (each an app can register in its own hooks.py):
#   wms_putaway_strategies = {"My strategy": "myapp.wms.rank_bins"}     function (bins, context) -> bins, best first; used by a Bin Determination Rule with strategy Custom
#   wms_removal_strategies  (above) - used by a Removal Rule with strategy Custom
#   wms_wocr_group_key = ["myapp.wms.wo_group"]                         function (task) -> str|None; tasks with different values never share a Warehouse Order
#   wms_queue_override = ["myapp.wms.pick_queue"]                       function (task, queue) -> queue|None
#   wms_ppf_actions = {"My action": "myapp.wms.do_it"}                 function (doc, params) for a PPF action of type Call Method
#   wms_mfs_handlers = {"My handler": "myapp.wms.on_plc"}               function (values, telegram_type) for an inbound MFS telegram with action Call Method

permission_query_conditions = {
    "WMS Stock Ledger Entry": "frappe_wms.permissions.ledger_query",
    "Warehouse Task": "frappe_wms.permissions.task_query",
}

has_permission = {
    "WMS Stock Ledger Entry": "frappe_wms.permissions.ledger_has_permission",
    "WMS Stock Balance": "frappe_wms.permissions.balance_has_permission",
}
