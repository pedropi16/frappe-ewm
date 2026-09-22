import frappe
from frappe import _

# Field(s) on each doctype that carry a warehouse, and what to call the WMS-side
# transaction that should be used instead of posting here directly. "level" says
# whether those fields live on the child item rows or the document itself.
# "condition_field" (Sales/Purchase Invoice) only guards stock when it's truthy.
_WAREHOUSE_FIELDS = {
    "Stock Entry": {"fields": ("s_warehouse", "t_warehouse"), "level": "item", "alt": "a Goods Receipt / Goods Issue / Move in the WMS app"},
    "Delivery Note": {"fields": ("warehouse", "target_warehouse"), "level": "item", "alt": "a Goods Issue in the WMS app"},
    "Purchase Receipt": {"fields": ("warehouse", "rejected_warehouse", "from_warehouse"), "level": "item", "alt": "a Goods Receipt in the WMS app"},
    "Stock Reconciliation": {"fields": ("warehouse",), "level": "item", "alt": "a WMS Physical Inventory Count"},
    "Sales Invoice": {"fields": ("warehouse",), "level": "item", "condition_field": "update_stock", "alt": "a Goods Issue in the WMS app"},
    "Purchase Invoice": {"fields": ("warehouse", "rejected_warehouse"), "level": "item", "condition_field": "update_stock", "alt": "a Goods Receipt in the WMS app"},
    "Subcontracting Receipt": {"fields": ("warehouse",), "level": "item", "alt": "a Goods Receipt in the WMS app"},
    "Subcontracting Order": {"fields": ("warehouse",), "level": "item", "alt": "a Goods Receipt in the WMS app"},
    # Work Order/Job Card themselves are deliberately NOT guarded (P2: production supply
    # legitimately plans a WMS-managed warehouse as a Work Order's source/wip/fg warehouse -
    # that's the whole point of frappe_wms.events.work_order.on_submit staging material for
    # it). Neither doctype posts stock directly anyway - the Stock Entries they spawn
    # (Material Transfer for Manufacture, Manufacture) are still fully guarded above via
    # s_warehouse/t_warehouse, same as every other stock posting.
}


def _enforcement_enabled():
    return bool(frappe.get_cached_value("WMS Settings", "WMS Settings", "enforce_wms_only_stock_movements"))


def _wms_managed_warehouses(erpnext_warehouses):
    if not erpnext_warehouses:
        return set()
    rows = frappe.get_all(
        "WMS Warehouse",
        filters={"erpnext_warehouse": ["in", list(erpnext_warehouses)]},
        pluck="erpnext_warehouse",
    )
    return set(rows)


def validate(doc, method=None):
    if doc.flags.get("wms_managed_posting"):
        return
    if not _enforcement_enabled():
        return
    config = _WAREHOUSE_FIELDS.get(doc.doctype)
    if not config:
        return
    if config.get("condition_field") and not doc.get(config["condition_field"]):
        return

    warehouses = set()
    if config["level"] == "item":
        for row in doc.get("items") or []:
            for field in config["fields"]:
                value = row.get(field)
                if value:
                    warehouses.add(value)
    else:
        for field in config["fields"]:
            value = doc.get(field)
            if value:
                warehouses.add(value)

    managed = _wms_managed_warehouses(warehouses)
    if managed:
        frappe.throw(
            _(
                "Warehouse {0} is managed by frappe_wms. Direct {1} postings against it are "
                "blocked - use {2} instead so the WMS stock ledger stays authoritative."
            ).format(frappe.bold(", ".join(sorted(managed))), doc.doctype, config["alt"]),
            title=_("WMS-Managed Warehouse"),
        )
