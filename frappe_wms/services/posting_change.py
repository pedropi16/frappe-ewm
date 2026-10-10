import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import OWNER_KEYS, _balance_name, transfer_stock
from frappe_wms.utils import require_role

# SAP EWM's posting change: the stock stays where it is (same bin / HU) and only changes its identity - stock type (AVAILABLE <-> WAREHOUSE_BLOCKED, a
# manual release after a recall hold, ...), owner / party entitled to dispose, product, batch, country of origin, special stock. An auditable
# document for every such change that has no Quality Inspection to hang it on.

POSTING_CHANGE_ROLES = ("WMS Supervisor", "WMS Inventory Controller")
# what the document changes besides the stock type: (doctype field of the stock, "change to" field)
ATTRIBUTES = tuple((k, f"to_{k}") for k in OWNER_KEYS if k != "consolidation_group")


def sides(doc):
    """(before, after) identity of the stock a posting change moves: product, batch, stock type and the owner / attribute dimensions."""
    before = {"product": doc.product, "batch_no": doc.batch_no or None, "stock_type": doc.from_stock_type, **{k: doc.get(k) or "" for k in OWNER_KEYS}}
    after = dict(before)
    after["stock_type"] = doc.to_stock_type or doc.from_stock_type
    if doc.get("to_product"):
        after["product"] = doc.to_product
        after["batch_no"] = doc.get("to_batch_no") or None  # a batch belongs to its product
    elif doc.get("to_batch_no"):
        after["batch_no"] = doc.to_batch_no
    for key, to_key in ATTRIBUTES:
        if doc.get(to_key): after[key] = doc.get(to_key)
    return before, after


def _stock_uom(product):
    return frappe.db.get_value("WMS Product", {"item": product}, "stock_uom") or frappe.db.get_value("Item", product, "stock_uom")


def _move(doc, forward, key):
    before, after = sides(doc)
    one, two = (before, after) if forward else (after, before)
    where = {"handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin}
    source = {"warehouse": doc.warehouse, "product": one["product"], "batch_no": one["batch_no"], "serial_no": doc.serial_no, "stock_uom": _stock_uom(one["product"]) or doc.stock_uom,
              **where, "stock_type": one["stock_type"], **{k: one[k] for k in OWNER_KEYS}}
    destination = {**where, "product": two["product"], "batch_no": two["batch_no"], "stock_type": two["stock_type"], "stock_uom": _stock_uom(two["product"]) or doc.stock_uom,
                   **{k: two[k] for k in OWNER_KEYS}}
    transfer_stock(source=source, destination=destination, quantity=doc.quantity, movement_type="501", reference_doctype=doc.doctype, reference_name=doc.name, idempotency_key=key)


def validate_change(doc):
    before, after = sides(doc)
    if before == after: frappe.throw(_("Nothing changes: choose a new stock type, owner, product, batch or attribute"))
    if (after["product"] != before["product"] or after["batch_no"] != before["batch_no"]) and doc.serial_no:
        frappe.throw(_("A serial number belongs to its product and batch: it cannot be changed by a posting change"))
    if after["product"] != before["product"] and not frappe.db.exists("WMS Product", {"item": after["product"], "warehouse_managed": 1}):
        frappe.throw(_("{0} is not a warehouse-managed product").format(after["product"]))
    if after["batch_no"] and frappe.db.get_value("Batch", after["batch_no"], "item") != after["product"]:
        frappe.throw(_("Batch {0} is not a batch of {1}").format(after["batch_no"], after["product"]))
    if after["special_stock_type"] and not after["special_stock_ref"] or after["special_stock_ref"] and not after["special_stock_type"]:
        frappe.throw(_("Special stock needs both its type and its reference"))
    balance = frappe.db.get_value("WMS Stock Balance", _balance_name({
        "warehouse": doc.warehouse, "product": before["product"], "batch_no": before["batch_no"], "serial_no": doc.serial_no, "handling_unit": doc.handling_unit,
        "storage_bin": doc.storage_bin, "stock_type": before["stock_type"], **{k: before[k] for k in OWNER_KEYS}}), ["quantity", "available_quantity"], as_dict=True)
    if not balance or flt(balance.available_quantity) < flt(doc.quantity) - 0.000001:
        frappe.throw(_("Only {0} {1} of this stock is free to change (allocated stock stays as it is)").format(flt(balance.available_quantity) if balance else 0, doc.product))


def _check_mixing(doc):
    """A partial change leaves the rest of the line in the bin under the old identity: where the storage type forbids mixing, the bin may not end up with both.
    Only the dimensions the change touches are checked, so a bin that was already mixed does not block an unrelated change."""
    if not doc.storage_bin: return
    from frappe_wms.services.bin_rules import mixing_violations
    before, after = sides(doc)
    storage_type = frappe.get_cached_doc("Storage Type", frappe.db.get_value("Storage Bin", doc.storage_bin, "storage_type"))
    reasons = mixing_violations(doc.storage_bin, storage_type, *(after[k] if after[k] != before[k] else None for k in ("product", "stock_type", "batch_no")), lock=True)
    if reasons:
        frappe.throw(_("Storage Bin {0} would end up mixed: {1}. Change the whole quantity, or give the changed stock a destination bin").format(doc.storage_bin, "; ".join(reasons)))


def post_posting_change(name, check_mixing=True):
    require_role(*POSTING_CHANGE_ROLES)
    doc = frappe.get_doc("WMS Posting Change", name, for_update=True)
    if doc.status != "Draft":
        frappe.throw(_("This posting change has already been posted or cancelled"))
    validate_change(doc)
    _move(doc, True, f"PSC:{doc.name}")
    if check_mixing: _check_mixing(doc)
    from frappe_wms.services.erp_sync_queue import dispatch
    doc.db_set({"status": "Posted", "posted_by": frappe.session.user, "posted_at": now_datetime()}, update_modified=True)
    dispatch("posting_change", doc)
    erpnext_se = frappe.db.get_value(doc.doctype, doc.name, "erpnext_stock_entry")
    return {"posting_change": doc.name, "status": "Posted", "erpnext_stock_entry": erpnext_se}


def cancel_posting_change(name):
    require_role(*POSTING_CHANGE_ROLES)
    doc = frappe.get_doc("WMS Posting Change", name, for_update=True)
    if doc.status != "Posted":
        frappe.throw(_("Only a posted posting change can be cancelled"))
    _move(doc, False, f"PSC-REV:{doc.name}")  # the reverse move
    if doc.erpnext_stock_entry:
        se = frappe.get_doc("Stock Entry", doc.erpnext_stock_entry)
        if se.docstatus == 1:
            se.flags.ignore_permissions = True
            se.flags.wms_managed_posting = True
            se.cancel()
    doc.db_set({"status": "Cancelled"}, update_modified=True)
    return {"posting_change": doc.name, "status": "Cancelled"}


def create_posting_change(line, reason, to_stock_type=None, changes=None):
    """A draft posting change for one stock line (a WMS Stock Balance row as the Monitor shows it) with quantity (default: all that is free)."""
    require_role(*POSTING_CHANGE_ROLES)
    changes = {k: v for k, v in (changes or {}).items() if v and k in {t for _k, t in ATTRIBUTES} | {"to_product", "to_batch_no"}}
    balance = frappe.get_doc("WMS Stock Balance", line["name"])
    doc = frappe.get_doc({"doctype": "WMS Posting Change", "warehouse": balance.warehouse, "product": balance.product, "batch_no": balance.batch_no, "serial_no": balance.serial_no,
        "handling_unit": balance.handling_unit, "storage_bin": balance.storage_bin, "from_stock_type": balance.stock_type, "to_stock_type": to_stock_type or None,
        "quantity": flt(line.get("quantity")) or flt(balance.available_quantity), "stock_uom": balance.stock_uom, "reason": reason, "status": "Draft",
        **{k: balance.get(k) for k in OWNER_KEYS if k != "consolidation_group"}, **changes})
    doc.insert(ignore_permissions=True)
    return doc.name


MOVE_KEYS = ("destination_bin", "process_type", "destination_storage_type", "destination_section")


def _after_balance(doc):
    """The stock balance the posted change leaves behind (new identity, same bin / HU) - what a following move task works on."""
    _before, after = sides(doc)
    return _balance_name({"warehouse": doc.warehouse, "product": after["product"], "batch_no": after["batch_no"], "serial_no": doc.serial_no, "handling_unit": doc.handling_unit,
                          "storage_bin": doc.storage_bin, "stock_type": after["stock_type"], **{k: after[k] for k in OWNER_KEYS}})


def _apply(line, defaults_batch, dry=False):
    """Creates and posts the line's change; when the line has a destination (bin / process type / storage type / section) the changed stock is moved there with an
    Internal Move task (confirmed at once on request) - SAP: a posting change that also relocates the stock creates a warehouse task. dry: resolves only, the caller rolls back.
    -> (document, tasks, plan or None)"""
    from frappe_wms.services import adhoc_tasks
    changes = {k: line.get(k) for k in ("to_product", "to_batch_no", "to_stock_owner", "to_entitled_party", "to_country_of_origin", "to_special_stock_type", "to_special_stock_ref")}
    name = create_posting_change(line, line.get("reason"), line.get("to_stock_type"), changes)
    moving = any(line.get(k) for k in MOVE_KEYS)
    post_posting_change(name, check_mixing=not moving)  # a destination takes the changed stock out of the bin: its task checks the destination
    if not moving: return name, [], None
    core = {"name": _after_balance(frappe.get_doc("WMS Posting Change", name)), "quantity": flt(line.get("quantity")) or None}
    args = (line.get("destination_bin"), line.get("priority") or "Normal", line.get("process_type"), line.get("reason"))
    if dry:
        return name, [], adhoc_tasks._plan([core], *args[:3], args[3], None, None, 0, line.get("destination_storage_type"), line.get("destination_section"), dry=True)[0]
    tasks, plans = adhoc_tasks._create([core], *args, 1 if line.get("confirm") else 0, defaults_batch, None, 0, line.get("destination_storage_type"), line.get("destination_section"))
    return name, tasks, plans[0]


def _plan_text(plan):
    return {k: plan[k] for k in ("process_type", "process_type_name", "destination_bin", "destination_storage_type", "destination_section")} if plan else {}


def check_lines(lines):
    """The worklist's Enter: what each line would do - the change, and where the stock goes when a destination / process type is given - or why it is refused. Nothing stays."""
    from frappe_wms.services.locks import require_free_many
    from frappe_wms.services.packing_center import _fail_text
    out = []
    for i, line in enumerate(lines):
        savepoint = f"pcw_chk_{i}"
        frappe.db.savepoint(savepoint)
        try:
            require_free_many([("WMS Stock Balance", line["name"])])
            name, _tasks, plan = _apply(line, None, dry=True)
            doc = frappe.get_doc("WMS Posting Change", name)
            before, after = sides(doc)
            changed = ", ".join(f"{k.replace('_', ' ')} {before[k] or '-'} \u2192 {after[k] or '-'}" for k in after if before[k] != after[k])
            out.append({"line": i, "ok": 1, "summary": changed, **_plan_text(plan)})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            out.append({"line": i, "ok": 0, "error": _fail_text(e)})
        finally:
            frappe.db.rollback(save_point=savepoint)
    return out


def process_lines(lines):
    """The posting change worklist's Create: every line carries its own new values, reason and optional destination, goes through on its own (a locked or refused line does not stop the rest).
    -> {"created": [{"line": i, "documents": [name], "tasks": [...], destination...}], "errors": [{"line": i, "error": text}]}"""
    from frappe_wms.services.locks import require_free_many
    from frappe_wms.services.packing_center import _fail_text
    batch, out = frappe.generate_hash(length=10), {"created": [], "errors": []}
    for i, line in enumerate(lines):
        savepoint = f"pcw_{i}"
        frappe.db.savepoint(savepoint)
        try:
            require_free_many([("WMS Stock Balance", line["name"])])
            name, tasks, plan = _apply(line, batch)
            out["created"].append({"line": i, "documents": [name], "tasks": tasks, "balance": _after_balance(frappe.get_doc("WMS Posting Change", name)), "foreground": [t for t in tasks if frappe.db.get_value("Warehouse Task", t, "status") != "Confirmed"] if line.get("confirm") else [], **_plan_text(plan)})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            frappe.db.rollback(save_point=savepoint)
            out["errors"].append({"line": i, "error": _fail_text(e)})
    return out
