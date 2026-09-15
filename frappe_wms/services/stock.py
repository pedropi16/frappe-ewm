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

def rebuild_balances(warehouse=None, product=None):
    # The ledger is the immutable source of truth; balances are a derived, rebuildable
    # projection of it. allocated_quantity is NOT derived here (allocations aren't part of
    # the ledger) - existing reservations on a balance row are preserved across a rebuild.
    filters = {}
    if warehouse: filters["warehouse"] = warehouse
    if product: filters["product"] = product
    condition_sql = " and ".join(f"`{k}`=%({k})s" for k in filters) or "1=1"
    rows = frappe.db.sql(f"""
        select warehouse, product, batch_no, serial_no, handling_unit, storage_bin, stock_type,
               sum(quantity) as quantity, max(stock_uom) as stock_uom,
               min(case when quantity > 0 then posting_datetime end) as first_receipt_date,
               max(posting_datetime) as last_movement_date
        from `tabWMS Stock Ledger Entry`
        where {condition_sql}
        group by warehouse, product, batch_no, serial_no, handling_unit, storage_bin, stock_type
    """, filters, as_dict=True)
    touched = set()
    for row in rows:
        name = _balance_name(row)
        touched.add(name)
        _lock_balance(name)
        doc = frappe.get_doc("WMS Stock Balance", name) if frappe.db.exists("WMS Stock Balance", name) else frappe.new_doc("WMS Stock Balance")
        if doc.is_new():
            doc.name = name
            for key in DIMENSIONS: doc.set(key, row.get(key))
            doc.allocated_quantity = 0
        doc.quantity = flt(row.quantity)
        doc.stock_uom = row.stock_uom
        doc.first_receipt_date = row.first_receipt_date
        doc.last_movement_date = row.last_movement_date
        doc.available_quantity = flt(doc.quantity) - flt(doc.allocated_quantity)
        doc.version = (doc.version or 0) + 1
        doc.flags.ignore_permissions = True
        doc.save()
    # Any existing balance row in scope with no ledger activity at all should read zero.
    for name in frappe.get_all("WMS Stock Balance", filters=filters, pluck="name"):
        if name in touched: continue
        allocated = flt(frappe.db.get_value("WMS Stock Balance", name, "allocated_quantity"))
        frappe.db.set_value("WMS Stock Balance", name, {"quantity": 0, "available_quantity": -allocated})
    return sorted(touched)
