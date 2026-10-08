import hashlib
import frappe
from frappe import _
from frappe.utils import flt, now_datetime

DIMENSIONS = ("warehouse", "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type")
# Ownership dimensions (SAP's owner and party entitled to dispose): part of a balance's identity only when set, so the identity (and name) of every
# balance of owner-less stock stays exactly what it always was.
# Stock attributes (country of origin, special stock = reserved to a sales order / project) behave the same way: identity only when set.
ATTR_KEYS = ("country_of_origin", "special_stock_type", "special_stock_ref")
# The consolidation group is a stock attribute too, but a transient one: the stock carries it while it is staged to a document (outbound delivery /
# production material request) in a consolidation group.
OWNER_KEYS = ("stock_owner", "entitled_party") + ATTR_KEYS + ("consolidation_group",)


def dim_values(obj):
    """The owner / attribute dimensions of a task, request, allocation or balance row, as an entry fragment."""
    return {k: obj.get(k) for k in OWNER_KEYS}


def _balance_name(values):
    raw = "|".join(str(values.get(k) or "") for k in DIMENSIONS)
    if values.get("stock_owner") or values.get("entitled_party"):
        raw += f"|{values.get('stock_owner') or ''}|{values.get('entitled_party') or ''}"
    if any(values.get(k) for k in ATTR_KEYS):
        raw += "|" + "|".join(str(values.get(k) or "") for k in ATTR_KEYS)
    if values.get("consolidation_group"):
        raw += f"|CG|{values['consolidation_group']}"
    return hashlib.sha256(raw.encode()).hexdigest()

def _owner_stock_exists():
    flag = frappe.flags.get("wms_owner_stock")
    if flag is None:
        flag = frappe.flags.wms_owner_stock = bool(frappe.db.sql("select 1 from `tabWMS Stock Balance` where """ + " or ".join(f"ifnull({k}, '') != ''" for k in OWNER_KEYS) + " limit 1"))
    return flag

def _resolve_owner(entry):
    """A decrement that does not name every owner / attribute dimension takes the missing ones from the stock it is booked from: the plain stock when it
    covers the quantity, else the one combination that holds it; several combinations holding it in the same place is ambiguous and must be said
    explicitly (stock_owner / entitled_party / country_of_origin / special_stock_*)."""
    missing = [k for k in OWNER_KEYS if k not in entry]
    if not missing or flt(entry.get("quantity")) >= 0 or not _owner_stock_exists(): return
    given = " and ".join(f"ifnull({k},'')=%({k})s" for k in OWNER_KEYS if k in entry)
    rows = frappe.db.sql(f"""select {", ".join(OWNER_KEYS)}, quantity from `tabWMS Stock Balance` where warehouse=%(warehouse)s and product=%(product)s and ifnull(batch_no,'')=%(batch_no)s
        and ifnull(serial_no,'')=%(serial_no)s and ifnull(handling_unit,'')=%(handling_unit)s and storage_bin=%(storage_bin)s and stock_type=%(stock_type)s and quantity > 0
        {"and " + given if given else ""}""", {**{k: entry.get(k) or "" for k in OWNER_KEYS}, "warehouse": entry["warehouse"], "product": entry["product"], "batch_no": entry.get("batch_no") or "",
        "serial_no": entry.get("serial_no") or "", "handling_unit": entry.get("handling_unit") or "", "storage_bin": entry["storage_bin"], "stock_type": entry["stock_type"]}, as_dict=True)
    needed = -flt(entry["quantity"])
    pool = [r for r in rows if flt(r.quantity) >= needed - 0.000001] or rows
    if not pool or any(not any(r.get(k) for k in missing) for r in pool): return
    if len(pool) > 1 and len({tuple(r.get(k) for k in missing) for r in pool}) > 1:
        frappe.throw(_("{0} in {1} belongs to several owners / origins: say which one ({2})").format(entry["product"], entry["storage_bin"],
            ", ".join("/".join(str(r.get(k) or "-") for k in OWNER_KEYS) for r in pool)))
    for k in missing: entry[k] = pool[0].get(k)

def _lock_balance(name):
    frappe.db.sql("select name from `tabWMS Stock Balance` where name=%s for update", name)

def _get_balance_for_update(name):
    # A locking read of the row itself (get_doc for_update), never "lock, then plain get_doc":
    # Frappe runs MariaDB at REPEATABLE READ, where a plain read after waiting on a row lock
    # returns the transaction's older snapshot - quantity as it was before whichever concurrent
    # posting just released the lock. Reproduced under concurrent picking as a
    # TimestampMismatchError on WMS Stock Balance (Frappe's own modified-check caught the stale
    # read); anything without that safety net would have silently lost the other posting.
    if frappe.db.sql("select name from `tabWMS Stock Balance` where name=%s for update", name):
        return frappe.get_doc("WMS Stock Balance", name, for_update=True)
    return None

def _upsert_balance(values, delta):
    name = _balance_name(values)
    doc = _get_balance_for_update(name) or frappe.new_doc("WMS Stock Balance")
    if doc.is_new():
        doc.name = name
        for key in DIMENSIONS + OWNER_KEYS: doc.set(key, values.get(key))
        if any(values.get(k) for k in OWNER_KEYS): frappe.flags.wms_owner_stock = True
        doc.stock_uom = values.get("stock_uom")
        doc.quantity = 0
        doc.allocated_quantity = 0
        # A carried first_receipt_date/shelf_life_expiry_date (transfer_stock passes these
        # through from the source balance row - the same physical stock, just relocated) wins
        # over "now"/blank - only a genuine external receipt with nothing to carry forward gets
        # today's date. Reproduced: every bin-to-bin move used to reset the GR date to the move
        # time and drop the expiry date entirely, so FIFO/FEFO/SLED all silently ignored any
        # stock that had ever been moved once.
        doc.first_receipt_date = values.get("first_receipt_date") or (now_datetime() if delta > 0 else None)
        doc.shelf_life_expiry_date = values.get("shelf_life_expiry_date")
    else:
        # Merging into an already-existing balance row (two partial moves landing in the same
        # bin, or stock already there): keep whichever date is actually older/soonest, so the
        # oldest stock physically present is still what FIFO/FEFO see first.
        incoming_receipt = values.get("first_receipt_date")
        if incoming_receipt and (not doc.first_receipt_date or incoming_receipt < doc.first_receipt_date):
            doc.first_receipt_date = incoming_receipt
        incoming_expiry = values.get("shelf_life_expiry_date")
        if incoming_expiry and (not doc.shelf_life_expiry_date or incoming_expiry < doc.shelf_life_expiry_date):
            doc.shelf_life_expiry_date = incoming_expiry
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
    # Entries are stored as "<key>:<seq>", so a replay is detected by the first entry's key - matching the bare
    # key never hit, and a retried request fell through to the unique index as a raw duplicate-entry error.
    if frappe.db.exists("WMS Stock Ledger Entry", {"idempotency_key": f"{idempotency_key}:1"}):
        return frappe.get_all("WMS Stock Ledger Entry", filters={"idempotency_key": ["like", f"{idempotency_key}:%"]}, pluck="name", order_by="creation asc")
    if round(sum(flt(x["quantity"]) for x in entries), 6) != 0 and len(entries) > 1:
        frappe.throw(_("Transfer postings must balance to zero"))
    entries = [dict(e) for e in entries]
    for e in entries: _resolve_owner(e)
    # Lock every balance row this batch will touch up front, in one globally-consistent sorted
    # order - not the order entries happen to be listed in - so opposite-direction transfers
    # between the same two rows can never deadlock waiting on each other. Any remaining,
    # unavoidable deadlock aborts the whole request's transaction, so it's retried at the request
    # level (services/concurrency.retry_on_deadlock on every API endpoint), never from here: an
    # in-place retry of just this function would re-post these entries on top of a transaction
    # whose earlier writes (the Goods Receipt, the task update...) InnoDB already rolled back.
    for name in sorted({_balance_name(e) for e in entries}):
        _lock_balance(name)
    for entry in entries:
        # A serial number identifies exactly one physical unit - any entry carrying one that
        # moves more or less than 1 is a data-entry mistake, not a real serialized movement
        # (reproduced by reading the code: a receipt line could freely claim quantity 3 against
        # a single serial number, silently pretending 3 units share one serial). Checked once
        # here rather than at every individual call site, since every stock change - receipt,
        # issue, internal move, repack, count - posts through this one function.
        if entry.get("serial_no") and round(abs(flt(entry["quantity"])), 6) != 1:
            frappe.throw(_("Serial {0} must move exactly 1 unit at a time (got {1})").format(entry["serial_no"], entry["quantity"]))
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
    # Deferred import: services/handling_unit.py -> services/task.py -> services/stock.py would
    # otherwise cycle at module load time.
    from frappe_wms.services.handling_unit import recompute_measurements
    for hu in {e.get("handling_unit") for e in entries if e.get("handling_unit")}:
        recompute_measurements(hu)
    return created

def _hu_and_descendants(hu_name):
    names = [hu_name]
    for child in frappe.get_all("Handling Unit", filters={"parent_hu": hu_name}, pluck="name"):
        names += _hu_and_descendants(child)
    return names

def relocate_hu_balances(hu_name, new_bin):
    # storage_bin is baked into a WMS Stock Balance row's identity key even when the stock is
    # HU-managed, so any operation that changes a Handling Unit's current_bin without posting a
    # ledger movement (nest/unnest, a direct HU-to-bin move) must migrate its balance rows itself
    # or they go stale at the old bin - reproduced in production: an HU nested at one bin, then
    # given more stock at its new bin, left the same product split across two rows (70 at the new
    # bin, 30 orphaned at the old one). Mirrors SAP EWM's own MOVE_HU, which "manages stock
    # accordingly" when an HU is relocated. No ledger entries here, same as nest/unnest themselves
    # - this only keeps the balance table's bookkeeping honest about where the HU actually is.
    for hu in _hu_and_descendants(hu_name):
        rows = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu, "storage_bin": ["!=", new_bin]}, fields=[
            "name", "warehouse", "product", "batch_no", "serial_no", "stock_type", *OWNER_KEYS,
            "quantity", "allocated_quantity", "stock_uom", "first_receipt_date", "shelf_life_expiry_date",
        ])
        for row in rows:
            new_values = {"warehouse": row.warehouse, "product": row.product, "batch_no": row.batch_no,
                          "serial_no": row.serial_no, "handling_unit": hu, "storage_bin": new_bin, "stock_type": row.stock_type,
                          **{k: row.get(k) for k in OWNER_KEYS}}
            new_name = _balance_name(new_values)
            for name in sorted({row.name, new_name}):
                _lock_balance(name)
            # Re-read the source row under the lock: the get_all above is a snapshot read.
            source_row = _get_balance_for_update(row.name)
            if not source_row: continue
            row.quantity, row.allocated_quantity = source_row.quantity, source_row.allocated_quantity
            target = _get_balance_for_update(new_name) or frappe.new_doc("WMS Stock Balance")
            if target.is_new():
                target.name = new_name
                for key in DIMENSIONS + OWNER_KEYS: target.set(key, new_values.get(key))
                target.stock_uom = row.stock_uom
                target.quantity = 0
                target.allocated_quantity = 0
                target.first_receipt_date = None
            target.quantity = flt(target.quantity) + flt(row.quantity)
            target.allocated_quantity = flt(target.allocated_quantity) + flt(row.allocated_quantity)
            target.available_quantity = flt(target.quantity) - flt(target.allocated_quantity)
            if row.first_receipt_date and (not target.first_receipt_date or row.first_receipt_date < target.first_receipt_date):
                target.first_receipt_date = row.first_receipt_date
            target.shelf_life_expiry_date = target.shelf_life_expiry_date or row.shelf_life_expiry_date
            target.last_movement_date = now_datetime()
            target.version = (target.version or 0) + 1
            target.flags.ignore_permissions = True
            target.save()
            frappe.delete_doc("WMS Stock Balance", row.name, ignore_permissions=True, force=True)

def transfer_stock(*, source, destination, quantity, movement_type, reference_doctype, reference_name, idempotency_key, warehouse_task=None, device=None):
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Transfer quantity must be greater than zero"))
    shared = {k: source.get(k) for k in ("warehouse", "product", "batch_no", "serial_no", "stock_uom")}
    # A transfer relocates existing stock - it must carry that stock's own GR date and shelf-life
    # expiry to wherever it lands, never reset them, whatever the destination row already looked
    # like (see _upsert_balance). Read before the source balance is decremented; if the caller
    # already knows better (a receipt inline with a transfer, e.g. inspection routing) its own
    # destination dict wins.
    # the stock keeps its owner wherever it goes: resolved on the source side, inherited by the destination unless that names its own
    probe = {**shared, "handling_unit": source.get("handling_unit"), "storage_bin": source.get("storage_bin"), "stock_type": source.get("stock_type"), "quantity": -quantity,
             **{k: source[k] for k in OWNER_KEYS if k in source}}
    _resolve_owner(probe)
    owner_values = {k: probe[k] for k in OWNER_KEYS if k in probe}
    source_name = _balance_name({**shared, "handling_unit": source.get("handling_unit"),
        "storage_bin": source.get("storage_bin"), "stock_type": source.get("stock_type"), **owner_values})
    carried = frappe.db.get_value("WMS Stock Balance", source_name, ["first_receipt_date", "shelf_life_expiry_date"], as_dict=True) or {}
    negative = {**shared, **source, **owner_values, "quantity": -quantity, "movement_type": movement_type}
    positive = {**shared, **owner_values, **destination, "quantity": quantity, "movement_type": movement_type,
        "first_receipt_date": destination.get("first_receipt_date") or carried.get("first_receipt_date"),
        "shelf_life_expiry_date": destination.get("shelf_life_expiry_date") or carried.get("shelf_life_expiry_date")}
    return post_entries([negative, positive], reference_doctype, reference_name, idempotency_key, warehouse_task, device)

def release_allocation(values, quantity):
    name = _balance_name(values)
    doc = _get_balance_for_update(name)
    if not doc: return
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
    owner_cols = ", ".join(OWNER_KEYS)
    rows = frappe.db.sql(f"""
        select warehouse, product, batch_no, serial_no, handling_unit, storage_bin, stock_type, {owner_cols},
               sum(quantity) as quantity, max(stock_uom) as stock_uom,
               min(case when quantity > 0 then posting_datetime end) as first_receipt_date,
               max(posting_datetime) as last_movement_date
        from `tabWMS Stock Ledger Entry`
        where {condition_sql}
        group by warehouse, product, batch_no, serial_no, handling_unit, storage_bin, stock_type, {owner_cols}
    """, filters, as_dict=True)
    touched = set()
    for row in rows:
        name = _balance_name(row)
        touched.add(name)
        doc = _get_balance_for_update(name) or frappe.new_doc("WMS Stock Balance")
        if doc.is_new():
            doc.name = name
            for key in DIMENSIONS + OWNER_KEYS: doc.set(key, row.get(key))
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
