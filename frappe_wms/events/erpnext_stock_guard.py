import frappe
from frappe import _

# Field(s) on each doctype that carry a warehouse, and what to call the WMS-side
# transaction that should be used instead of posting here directly. "level" says
# whether those fields live on the child item rows or the document itself.
# "condition_field" (Sales/Purchase Invoice) only guards stock when it's truthy.
_WAREHOUSE_FIELDS = {
    "Stock Entry": {"fields": ("s_warehouse", "t_warehouse"), "level": "item", "alt": "a Goods Receipt / Goods Issue / Move in the WMS app"},
    # packed_items: a product bundle's components carry their own warehouse.
    "Delivery Note": {"fields": ("warehouse", "target_warehouse"), "level": "item", "tables": ("items", "packed_items"), "alt": "a Goods Issue in the WMS app"},
    "Purchase Receipt": {"fields": ("warehouse", "rejected_warehouse", "from_warehouse"), "level": "item", "alt": "a Goods Receipt in the WMS app"},
    "Stock Reconciliation": {"fields": ("warehouse",), "level": "item", "alt": "a WMS Physical Inventory Count"},
    "Sales Invoice": {"fields": ("warehouse", "target_warehouse"), "level": "item", "tables": ("items", "packed_items"), "condition_field": "update_stock", "alt": "a Goods Issue in the WMS app"},
    "POS Invoice": {"fields": ("warehouse",), "level": "item", "tables": ("items", "packed_items"), "condition_field": "update_stock", "alt": "a Goods Issue in the WMS app (sell from a warehouse the WMS does not manage)"},
    "Purchase Invoice": {"fields": ("warehouse", "rejected_warehouse", "from_warehouse"), "level": "item", "condition_field": "update_stock", "alt": "a Goods Receipt in the WMS app"},
    "Subcontracting Receipt": {"fields": ("warehouse", "rejected_warehouse"), "level": "item", "alt": "a Goods Receipt in the WMS app"},
    "Subcontracting Order": {"fields": ("warehouse",), "level": "item", "alt": "a Goods Receipt in the WMS app"},
    # Assets consume stock: Asset Capitalization posts its own stock ledger entries, Asset Repair
    # a Stock Entry on submit - refused here, up front, with a message about the asset document.
    "Asset Capitalization": {"fields": ("warehouse",), "level": "item", "tables": ("stock_items",), "alt": "a Goods Issue in the WMS app first, then capitalize from a non-WMS warehouse"},
    "Asset Repair": {"fields": ("warehouse",), "level": "item", "tables": ("stock_items",), "alt": "a Goods Issue in the WMS app first, then consume from a non-WMS warehouse"},
    # Work Order/Job Card themselves are deliberately NOT guarded (P2: production supply
    # legitimately plans a WMS-managed warehouse as a Work Order's source/wip/fg warehouse -
    # that's the whole point of frappe_wms.events.work_order.on_submit staging material for
    # it). Neither doctype posts stock directly anyway - the Stock Entries they spawn
    # (Material Transfer for Manufacture, Manufacture) are still fully guarded above via
    # s_warehouse/t_warehouse, same as every other stock posting. Subcontracting Inward Order
    # likewise moves stock only through Stock Entries.
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
    if doc.flags.get("wms_managed_posting") or frappe.flags.get("wms_posting"):
        return
    # A draft Delivery Note / Purchase Receipt on a warehouse that replicates from drafts IS the
    # warehouse delivery (the ECC delivery / ASN): it may exist, and is submitted only by the
    # warehouse's own goods issue / goods receipt (erp_integration.before_draft_document_submit).
    if doc.doctype in ("Delivery Note", "Purchase Receipt") and doc.docstatus == 0 and not doc.get("is_return"):
        from frappe_wms.services.erp_integration import draft_document_is_replicated
        if draft_document_is_replicated(doc):
            return
    if doc.doctype == "Stock Entry" and doc.docstatus == 0:
        from frappe_wms.services.erp_integration import stock_entry_is_replicated
        if stock_entry_is_replicated(doc):
            return
    if doc.doctype in ("Delivery Note", "Purchase Receipt", "Stock Entry") and doc.docstatus == 1:
        from frappe_wms.services.erp_integration import before_draft_document_submit
        before_draft_document_submit(doc)
    if not _enforcement_enabled():
        return
    config = _WAREHOUSE_FIELDS.get(doc.doctype)
    if not config:
        return
    if config.get("condition_field") and not doc.get(config["condition_field"]):
        return

    warehouses = set()
    # Backflushed consumption of a Work Order with a Production Material Request: its materials leave the PSA's
    # warehouse in ERPNext and are booked out of the PSA in the WMS on submit (services/production_supply).
    skip = ()
    if doc.doctype == "Stock Entry":
        from frappe_wms.services.production_supply import stock_entry_is_pmr_consumption
        if stock_entry_is_pmr_consumption(doc): skip = ("s_warehouse",)
    if config["level"] == "item":
        for table in config.get("tables", ("items",)):
            for row in doc.get(table) or []:
                for field in (f for f in config["fields"] if f not in skip):
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


# Where the WMS stores the ERPNext documents it posted: (WMS doctype, field, holds a comma list).
_MIRROR_REFERENCES = {
    "Stock Entry": [("Goods Receipt", "erpnext_stock_entry", False), ("Goods Issue", "erpnext_stock_entry", False),
                    ("WMS Quality Inspection", "erpnext_stock_entry", False), ("WMS Posting Change", "erpnext_stock_entry", False),
                    ("Kitting Order", "erpnext_stock_entry", False), ("Warehouse Request", "erpnext_stock_entry", False),
                    ("WMS Task Difference", "erpnext_stock_entry", False),
                    ("WMS Physical Inventory Count", "erpnext_gain_stock_entry", True), ("WMS Physical Inventory Count", "erpnext_loss_stock_entry", True)],
    "Delivery Note": [("Goods Issue", "erpnext_delivery_note", False), ("Goods Receipt", "erpnext_delivery_note", False)],
    "Purchase Receipt": [("Goods Receipt", "erpnext_purchase_receipt", False), ("Goods Issue", "erpnext_purchase_receipt", False)],
    "Stock Reconciliation": [("WMS Opening Stock Load", "erpnext_stock_reconciliations", True)],
}


def wms_owner_of(doctype, name):
    """(WMS doctype, WMS document) that posted this ERPNext document, if any."""
    for wms_doctype, field, is_list in _MIRROR_REFERENCES.get(doctype, []):
        if is_list:
            hit = frappe.db.sql(f"select name from `tab{wms_doctype}` where concat(',', replace(ifnull(`{field}`,''), ' ', ''), ',') like %s limit 1",
                                (f"%,{name},%",))
            hit = hit[0][0] if hit else None
        else:
            hit = frappe.db.get_value(wms_doctype, {field: name})
        if hit: return wms_doctype, hit
    return None


def before_cancel(doc, method=None):
    """A document the warehouse posted is reversed from the warehouse side, never cancelled here:
    cancelling it directly would leave the WMS stock ledger and ERPNext's disagreeing."""
    if doc.flags.get("wms_managed_posting") or frappe.flags.get("wms_posting"):
        return
    owner = wms_owner_of(doc.doctype, doc.name)
    if owner:
        frappe.throw(_("{0} {1} was posted by the warehouse for {2} {3}. Reverse it there (the reversal cancels this document too).")
                     .format(doc.doctype, doc.name, _(owner[0]), frappe.bold(owner[1])), title=_("Warehouse-Managed Document"))
