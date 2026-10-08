import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.bin_rules import validate_destination_bin
from frappe_wms.services.handling_unit import get_or_create_handling_unit
from frappe_wms.services.stock import OWNER_KEYS, _balance_name, post_entries
from frappe_wms.utils import require_role

# Stock the warehouse learns about outside a delivery: scrapping (stock leaves as waste, SAP's scrapping posting) and an unplanned receipt (stock
# found / made / returned with no inbound delivery behind it). Both are documents with a mandatory reason, post through the one stock write path and are
# mirrored to ERPNext as a Material Issue / Material Receipt.

ADJUSTMENT_ROLES = ("WMS Supervisor", "WMS Inventory Controller")
MOVEMENT = {"Scrapping": "551", "Unplanned Receipt": "511"}


def _values(doc, sign):
    entry = {"warehouse": doc.warehouse, "product": doc.product, "batch_no": doc.batch_no, "serial_no": doc.serial_no, "handling_unit": doc.handling_unit,
             "storage_bin": doc.storage_bin, "stock_type": doc.stock_type, "quantity": sign * flt(doc.quantity), "stock_uom": doc.stock_uom, "movement_type": MOVEMENT[doc.adjustment_type]}
    entry.update({k: doc.get(k) or "" for k in OWNER_KEYS if k != "consolidation_group"})
    entry["consolidation_group"] = ""
    return entry


def post_stock_adjustment(name):
    require_role(*ADJUSTMENT_ROLES)
    doc = frappe.get_doc("WMS Stock Adjustment", name, for_update=True)
    if doc.status != "Draft": frappe.throw(_("This adjustment has already been posted or cancelled"))
    if flt(doc.quantity) <= 0: frappe.throw(_("Quantity must be greater than zero"))
    if frappe.db.get_value("Storage Bin", doc.storage_bin, "warehouse") != doc.warehouse:
        frappe.throw(_("Storage Bin {0} does not belong to warehouse {1}").format(doc.storage_bin, doc.warehouse))
    if doc.adjustment_type == "Scrapping":
        balance = frappe.db.get_value("WMS Stock Balance", _balance_name(_values(doc, -1)), "available_quantity")
        if flt(balance) < flt(doc.quantity) - 0.000001:
            frappe.throw(_("Only {0} {1} is free to scrap in {2} (allocated stock stays as it is)").format(flt(balance), doc.product, doc.storage_bin))
        sign = -1
    else:
        if doc.handling_unit:
            get_or_create_handling_unit(doc.handling_unit, None, doc.storage_bin, doc.warehouse)
        hu_type = frappe.db.get_value("Handling Unit", doc.handling_unit, "hu_type") if doc.handling_unit else None
        validate_destination_bin(doc.storage_bin, item=doc.product, stock_type=doc.stock_type, hu_type=hu_type, batch_no=doc.batch_no, destination_hu=doc.handling_unit)
        sign = 1
    post_entries([_values(doc, sign)], doc.doctype, doc.name, f"SADJ:{doc.name}")
    from frappe_wms.services.erp_sync_queue import dispatch
    doc.db_set({"status": "Posted", "posted_by": frappe.session.user, "posted_at": now_datetime()}, update_modified=True)
    dispatch("stock_adjustment", doc)
    return {"stock_adjustment": doc.name, "status": "Posted"}


def cancel_stock_adjustment(name):
    require_role(*ADJUSTMENT_ROLES)
    doc = frappe.get_doc("WMS Stock Adjustment", name, for_update=True)
    if doc.status != "Posted": frappe.throw(_("Only a posted adjustment can be cancelled"))
    from frappe_wms.services.archiving import ensure_reversible
    ensure_reversible(doc)
    sign = 1 if doc.adjustment_type == "Scrapping" else -1  # the opposite of what it posted
    entry = _values(doc, sign)
    entry["reversal_of"] = frappe.db.get_value("WMS Stock Ledger Entry", {"reference_doctype": doc.doctype, "reference_name": doc.name, "reversal_of": ["in", [None, ""]]}, "name")
    post_entries([entry], doc.doctype, doc.name, f"SADJ-REV:{doc.name}")
    from frappe_wms.services.erp_sync_queue import dispatch
    dispatch("stock_adjustment_reversal", doc)
    doc.db_set({"status": "Cancelled"}, update_modified=True)
    return {"stock_adjustment": doc.name, "status": "Cancelled"}


def create_stock_adjustment(adjustment_type, reason, line, quantity=None, post=True, **fields):
    """Scrapping of existing stock (line = a WMS Stock Balance row) or an unplanned receipt (line = where it goes: warehouse, product, storage_bin, ...)."""
    require_role(*ADJUSTMENT_ROLES)
    if not (reason or "").strip(): frappe.throw(_("A reason is required"))
    if adjustment_type == "Scrapping":
        balance = frappe.get_doc("WMS Stock Balance", line["name"])
        values = {"warehouse": balance.warehouse, "product": balance.product, "batch_no": balance.batch_no, "serial_no": balance.serial_no, "handling_unit": balance.handling_unit,
                  "storage_bin": balance.storage_bin, "stock_type": balance.stock_type, "stock_uom": balance.stock_uom,
                  "quantity": flt(quantity) or flt(balance.available_quantity), **{k: balance.get(k) for k in OWNER_KEYS if k != "consolidation_group"}}
    else:
        uom = frappe.db.get_value("WMS Product", {"item": line["product"]}, "stock_uom") or frappe.db.get_value("Item", line["product"], "stock_uom")
        values = {"stock_uom": uom, "stock_type": "AVAILABLE", **line, "quantity": flt(quantity)}
    doc = frappe.get_doc({"doctype": "WMS Stock Adjustment", "adjustment_type": adjustment_type, "reason": reason, "status": "Draft", **values, **fields})
    doc.insert(ignore_permissions=True)
    if post: post_stock_adjustment(doc.name)
    return doc.name
