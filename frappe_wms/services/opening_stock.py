import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.services.bin_rules import validate_destination_bin
from frappe_wms.services.handling_unit import get_or_create_handling_unit
from frappe_wms.utils import require_role

# Cutover: load existing on-hand stock into WMS bin-by-bin (and mirror it to ERPNext) before
# go-live. There was no tool for this at all before - config import (setup/import_profile.py)
# only ever moved structure/rules, never quantities, so the only way to get real stock into a
# WMS-managed warehouse was one Goods Receipt at a time against a Purchase Order that likely
# never existed for it. Posts as SAP's own movement type 561 (goods receipt for initial entry of
# stock balances), the same idiom every other WMS posting already follows (services/stock.py is
# the one write path).

LOAD_ROLES = ("WMS Supervisor", "WMS Administrator")

# ERPNext's StockReconciliation.submit() queues anything larger than this as a background job.
SR_MAX_ROWS = 100


def _resolve_valuation_rate(item, given):
    if given:
        return flt(given)
    rate = flt(frappe.db.get_value("Item", item, "valuation_rate"))
    if not rate:
        rate = flt(frappe.db.get_value("Item", item, "last_purchase_rate"))
    return rate


def _resolve_opening_account(company):
    return (
        frappe.db.get_value("Company", company, "default_inventory_account")
        or frappe.db.get_value("Account", {"company": company, "account_type": "Stock", "is_group": 0}, "name")
    )


def post_opening_stock_load(name):
    # A one-time cutover action against live warehouse state, not an everyday transaction - the
    # same tier of risk as a data-repair script, so it's restricted to Supervisor/Administrator
    # rather than the ordinary operator roles every other posting here allows.
    require_role(*LOAD_ROLES)
    doc = frappe.get_doc("WMS Opening Stock Load", name, for_update=True)
    if doc.status != "Draft":
        frappe.throw(_("This load has already been posted or cancelled"))
    if not doc.items:
        frappe.throw(_("Add at least one line before posting"))

    warehouse = frappe.get_doc("WMS Warehouse", doc.warehouse)
    company = warehouse.company
    erpnext_warehouse = warehouse.erpnext_warehouse

    for i, row in enumerate(doc.items, 1):
        bin_warehouse = frappe.db.get_value("Storage Bin", row.storage_bin, "warehouse")
        if bin_warehouse != doc.warehouse:
            frappe.throw(_("Row {0}: Storage Bin {1} does not belong to warehouse {2}").format(row.idx, row.storage_bin, doc.warehouse))
        handling_unit = row.handling_unit
        if handling_unit:
            handling_unit = get_or_create_handling_unit(handling_unit, row.hu_type, row.storage_bin, doc.warehouse)
        hu_type = frappe.db.get_value("Handling Unit", handling_unit, "hu_type") if handling_unit else None
        validate_destination_bin(row.storage_bin, item=row.item, stock_type=row.stock_type, hu_type=hu_type,
            batch_no=row.batch_no, destination_hu=handling_unit)
        entry = {
            "warehouse": doc.warehouse, "product": row.item, "batch_no": row.batch_no, "serial_no": row.serial_no,
            "handling_unit": handling_unit, "storage_bin": row.storage_bin, "stock_type": row.stock_type,
            "quantity": row.quantity, "stock_uom": row.stock_uom, "movement_type": "561",
            "reference_line": row.name,
        }
        if row.shelf_life_expiry_date:
            entry["shelf_life_expiry_date"] = row.shelf_life_expiry_date
        post_entries([entry], doc.doctype, doc.name, f"OSL:{doc.name}:{i}")

    erpnext_srs = []
    if erpnext_warehouse:
        groups = {}
        for row in doc.items:
            key = (row.item, row.batch_no, row.serial_no, row.stock_type)
            group = groups.setdefault(key, {"quantity": 0.0, "stock_uom": row.stock_uom, "valuation_rate": row.valuation_rate})
            group["quantity"] += flt(row.quantity)
        account = _resolve_opening_account(company)
        if not account:
            frappe.throw(_("Could not resolve a Stock/Asset account for company {0} to post the opening ERPNext Stock Reconciliation - configure one and post again, or clear the warehouse's ERPNext link first").format(company))
        # Chunked on purpose: ERPNext's StockReconciliation.submit() hands any document with more
        # than SR_MAX_ROWS rows to a background job instead of submitting it in this request. That
        # job reloads the document from the database, losing the in-memory wms_managed_posting
        # flag, so the WMS stock guard (events/erpnext_stock_guard.py) rejected it and the
        # reconciliation silently stayed Draft - reproduced with a ~240-row cutover load: WMS
        # showed Posted while ERPNext never received a single unit of opening stock. Keeping each
        # document at or under the limit keeps every submit synchronous and inside this
        # transaction, so a failure rolls the whole load back instead of diverging the ledgers.
        group_items = list(groups.items())
        for start in range(0, len(group_items), SR_MAX_ROWS):
            sr = frappe.get_doc({"doctype": "Stock Reconciliation", "company": company, "purpose": "Opening Stock", "expense_account": account})
            for (item, batch_no, serial_no, stock_type), group in group_items[start:start + SR_MAX_ROWS]:
                sr.append("items", {
                    "item_code": item, "warehouse": erpnext_warehouse, "qty": group["quantity"],
                    "valuation_rate": _resolve_valuation_rate(item, group["valuation_rate"]),
                    "batch_no": batch_no, "serial_no": serial_no, "use_serial_batch_fields": 1,
                    "wms_stock_type": stock_type,
                })
            sr.flags.wms_managed_posting = True
            sr.insert(ignore_permissions=True)
            sr.submit()
            if sr.docstatus != 1:
                frappe.throw(_("ERPNext Stock Reconciliation {0} was not submitted").format(sr.name))
            erpnext_srs.append(sr.name)

    doc.db_set({
        "status": "Posted", "posted_by": frappe.session.user, "posted_at": now_datetime(),
        "erpnext_stock_reconciliations": ", ".join(erpnext_srs),
    }, update_modified=True)
    return {"opening_stock_load": doc.name, "status": "Posted",
            "erpnext_stock_reconciliation": erpnext_srs[0] if erpnext_srs else None,
            "erpnext_stock_reconciliations": erpnext_srs}


def cancel_opening_stock_load(name):
    require_role(*LOAD_ROLES)
    doc = frappe.get_doc("WMS Opening Stock Load", name, for_update=True)
    if doc.status != "Posted":
        frappe.throw(_("Only a posted load can be cancelled"))
    original = frappe.get_all("WMS Stock Ledger Entry", filters={"reference_doctype": doc.doctype, "reference_name": doc.name}, fields=["*"])
    for i, row in enumerate(original, 1):
        values = {k: row.get(k) for k in ("warehouse", "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type", "stock_uom")}
        values.update({"quantity": -row.quantity, "movement_type": "561", "reversal_of": row.name})
        post_entries([values], doc.doctype, doc.name, f"OSL-REV:{doc.name}:{i}")
    if doc.erpnext_stock_reconciliations:
        for sr_name in doc.erpnext_stock_reconciliations.split(","):
            sr_name = sr_name.strip()
            if not sr_name:
                continue
            sr = frappe.get_doc("Stock Reconciliation", sr_name)
            if sr.docstatus == 1:
                sr.flags.ignore_permissions = True
                sr.flags.wms_managed_posting = True
                sr.cancel()
    doc.db_set({"status": "Cancelled"}, update_modified=True)
    return {"opening_stock_load": doc.name, "status": "Cancelled"}


def list_draft_loads(warehouse=None):
    require_role(*LOAD_ROLES)
    filters = {"status": "Draft"}
    if warehouse:
        filters["warehouse"] = warehouse
    return frappe.get_list("WMS Opening Stock Load", filters=filters, fields=["name", "warehouse", "load_date", "remarks"], order_by="creation asc", limit=50)
