import frappe
from frappe import _

# Field(s) on each doctype's item row that carry a warehouse, and what to call
# the WMS-side transaction that should be used instead of posting here directly.
_WAREHOUSE_FIELDS = {
    "Stock Entry": (("s_warehouse", "t_warehouse"), "a Goods Receipt / Goods Issue / Move in the WMS app"),
    "Delivery Note": (("warehouse", "target_warehouse"), "a Goods Issue in the WMS app"),
    "Purchase Receipt": (("warehouse", "rejected_warehouse", "from_warehouse"), "a Goods Receipt in the WMS app"),
    "Stock Reconciliation": (("warehouse",), "a WMS Physical Inventory Count"),
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
    warehouse_fields, wms_alternative = _WAREHOUSE_FIELDS[doc.doctype]
    rows = doc.get("items") or []

    warehouses = set()
    for row in rows:
        for field in warehouse_fields:
            value = row.get(field)
            if value:
                warehouses.add(value)
    # Stock Entry also allows a document-level warehouse in some flows; item rows cover the
    # normal case, which is all that's needed here.

    managed = _wms_managed_warehouses(warehouses)
    if managed:
        frappe.throw(
            _(
                "Warehouse {0} is managed by frappe_wms. Direct {1} postings against it are "
                "blocked - use {2} instead so the WMS stock ledger stays authoritative."
            ).format(frappe.bold(", ".join(sorted(managed))), doc.doctype, wms_alternative),
            title=_("WMS-Managed Warehouse"),
        )
