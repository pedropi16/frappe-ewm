import hashlib
import frappe
from frappe import _
from frappe.utils import flt, now_datetime

DIMENSIONS = ("warehouse", "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type")

def _balance_name(values):
    raw = "|".join(str(values.get(k) or "") for k in DIMENSIONS)
    return hashlib.sha256(raw.encode()).hexdigest()

def _lock_balance(name):
    frappe.db.sql("select name from `tabWMS Stock Balance` where name=%s for update", name)

def _upsert_balance(values, delta):
    name = _balance_name(values)
    _lock_balance(name)
    doc = frappe.get_doc("WMS Stock Balance", name) if frappe.db.exists("WMS Stock Balance", name) else frappe.new_doc("WMS Stock Balance")
    if doc.is_new():
        doc.name = name
        for key in DIMENSIONS: doc.set(key, values.get(key))
        doc.stock_uom = values.get("stock_uom")
        doc.quantity = 0
        doc.allocated_quantity = 0
        doc.first_receipt_date = now_datetime() if delta > 0 else None
    new_qty = flt(doc.quantity) + flt(delta)
    warehouse = frappe.get_cached_doc("WMS Warehouse", values["warehouse"])
    if new_qty < 0 and not warehouse.allow_negative_stock:
        frappe.throw(_("Insufficient stock for {0} in bin {1}").format(values["product"], values["storage_bin"]))
    doc.quantity = new_qty
    doc.available_quantity = new_qty - flt(doc.allocated_quantity)
    doc.last_movement_date = now_datetime()
    doc.version = (doc.version or 0) + 1
    doc.flags.ignore_permissions = True
    doc.save()
    return doc

def post_entries(entries, reference_doctype, reference_name, idempotency_key, warehouse_task=None, device=None):
    if frappe.db.exists("WMS Stock Ledger Entry", {"idempotency_key": idempotency_key}):
        return frappe.get_all("WMS Stock Ledger Entry", filters={"idempotency_key": idempotency_key}, pluck="name")
    if round(sum(flt(x["quantity"]) for x in entries), 6) != 0 and len(entries) > 1:
        frappe.throw(_("Transfer postings must balance to zero"))
    created = []
    for seq, values in enumerate(entries, 1):
        payload = dict(values)
        _upsert_balance(payload, payload["quantity"])
        sle = frappe.new_doc("WMS Stock Ledger Entry")
        sle.update(payload)
        sle.posting_datetime = payload.get("posting_datetime") or now_datetime()
        sle.reference_doctype = reference_doctype
        sle.reference_name = reference_name
        sle.reference_line = payload.get("reference_line")
        sle.warehouse_task = warehouse_task
        sle.idempotency_key = f"{idempotency_key}:{seq}"
        sle.posting_user = frappe.session.user
        sle.posting_device = device
        sle.flags.ignore_permissions = True
        sle.insert()
        created.append(sle.name)
    return created

def transfer_stock(*, source, destination, quantity, movement_type, reference_doctype, reference_name, idempotency_key, warehouse_task=None, device=None):
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Transfer quantity must be greater than zero"))
    shared = {k: source.get(k) for k in ("warehouse", "product", "batch_no", "serial_no", "stock_uom")}
    negative = {**shared, **source, "quantity": -quantity, "movement_type": movement_type}
    positive = {**shared, **destination, "quantity": quantity, "movement_type": movement_type}
    return post_entries([negative, positive], reference_doctype, reference_name, idempotency_key, warehouse_task, device)

def release_allocation(values, quantity):
    name = _balance_name(values)
    _lock_balance(name)
    if not frappe.db.exists("WMS Stock Balance", name): return
    doc = frappe.get_doc("WMS Stock Balance", name)
    doc.allocated_quantity = max(flt(doc.allocated_quantity) - flt(quantity), 0)
    doc.available_quantity = flt(doc.quantity) - doc.allocated_quantity
    doc.flags.ignore_permissions = True
    doc.save()
