"""Production supply the SAP EWM way: one warehouse, Production Supply Areas (PSA) inside it, and a
Production Material Request (PMR) per production order listing the materials it needs.

* PMR: created when the Work Order is submitted (the CO01 analogue), one item per required material, each
  resolved to the PSA of its operation's workstation.
* Staging: stock is moved from a chosen storage bin into the PSA's supply bin by ordinary Warehouse Requests/
  Tasks (the Replenish pipeline). Single-order staging references one PMR item: the staged quantity is reserved
  to it and only it can consume it. Cross-order staging serves several PMR items with one movement: the stock
  sits in the PSA unreserved and any open PMR for that product can consume it.
* Consumption: backflushed from ERPNext's Manufacture / Material Consumption entry for the Work Order; the
  supply bin's stock is booked out, never more than was staged for that order (plus the unreserved pool).

The reservation is bookkeeping on the PMR item, not a stock dimension: a PSA bin may hold several orders' stock.
"""
import frappe
from frappe import _
from frappe.utils import flt, getdate

from frappe_wms.services.determination import determine_process_type
from frappe_wms.services.stock import post_entries
from frappe_wms.utils import require_role

OPEN_STATUSES = ("Open", "Partially Staged", "Staged", "Partially Consumed")
CONSUMING_PURPOSES = ("Manufacture", "Material Consumption for Manufacture")
STAGE_ROLES = ("WMS Operator", "WMS Supervisor", "WMS Picker")


# ------------------------------------------------------------------ PMR from the Work Order

def _wms_warehouse(erpnext_warehouse):
    return frappe.db.get_value("WMS Warehouse", {"erpnext_warehouse": erpnext_warehouse}, "name") if erpnext_warehouse else None


def resolve_psa(warehouse, workstation=None):
    """The PSA that supplies a workstation; with none given (or listed), the warehouse's only active PSA."""
    if workstation:
        for parent in frappe.get_all("PSA Workstation", filters={"workstation": workstation, "parenttype": "Production Supply Area"}, pluck="parent"):
            if frappe.db.get_value("Production Supply Area", parent, ["warehouse", "active"]) == (warehouse, 1): return parent
    only = frappe.get_all("Production Supply Area", filters={"warehouse": warehouse, "active": 1}, pluck="name", limit=2)
    return only[0] if len(only) == 1 else None


def create_pmr(wo):
    warehouse, items = None, []
    workstations = {o.operation: o.workstation for o in wo.get("operations") or []}
    default_ws = next(iter(workstations.values()), None)
    for row in wo.required_items:
        row_warehouse = _wms_warehouse(row.source_warehouse or wo.source_warehouse)
        if not row_warehouse or flt(row.required_qty) <= 0: continue
        if not frappe.db.exists("WMS Product", {"item": row.item_code, "warehouse_managed": 1}): continue
        warehouse = warehouse or row_warehouse
        items.append({"work_order_item": row.name, "product": row.item_code, "stock_uom": frappe.db.get_value("Item", row.item_code, "stock_uom"),
            "operation": row.get("operation"), "psa": resolve_psa(row_warehouse, workstations.get(row.get("operation")) or default_ws), "required_quantity": flt(row.required_qty)})
    if not items or not frappe.db.exists("Production Supply Area", {"warehouse": warehouse, "active": 1}): return None  # no PSA: the Work Order is staged directly (events/work_order)
    pmr = frappe.get_doc({"doctype": "Production Material Request", "work_order": wo.name, "warehouse": warehouse, "production_item": wo.production_item,
        "qty": wo.qty, "planned_date": getdate(wo.planned_start_date) if wo.get("planned_start_date") else None, "status": "Open", "items": items})
    pmr.insert(ignore_permissions=True)
    for psa in {i["psa"] for i in items if i["psa"] and frappe.db.get_value("Production Supply Area", i["psa"], "staging_mode") == "Automatic"}:
        auto_stage(psa)
    return pmr.name


def cancel_pmr(wo):
    for name in frappe.get_all("Production Material Request", filters={"work_order": wo.name, "status": ["!=", "Cancelled"]}, pluck="name"):
        frappe.db.set_value("Production Material Request", name, "status", "Cancelled")


# ------------------------------------------------------------------ reservation arithmetic

def _supply_bin(psa):
    return frappe.db.get_value("Production Supply Area", psa, "supply_bin")


def _psa_stock(psa, product, lock=False):
    rows = frappe.db.sql("select quantity from `tabWMS Stock Balance` where storage_bin=%s and product=%s and quantity>0" + (" for update" if lock else ""), (_supply_bin(psa), product))
    return sum(flt(r[0]) for r in rows)


def _reserved_outstanding(psa, product):
    row = frappe.db.sql("""select coalesce(sum(greatest(i.staged_quantity - i.consumed_quantity, 0)), 0) from `tabProduction Material Request Item` i
        join `tabProduction Material Request` p on p.name = i.parent where i.psa=%s and i.product=%s and p.status in %s""", (psa, product, OPEN_STATUSES))
    return flt(row[0][0])


def pool_available(psa, product, lock=False):
    """Stock in the PSA that no single order reserved - what cross-order staging put there."""
    return max(_psa_stock(psa, product, lock) - _reserved_outstanding(psa, product), 0)


def _refresh_status(pmr_name):
    items = frappe.db.sql("select required_quantity, tasked_quantity, consumed_quantity from `tabProduction Material Request Item` where parent=%s", pmr_name, as_dict=True)
    if not items or frappe.db.get_value("Production Material Request", pmr_name, "status") in ("Closed", "Cancelled"): return
    done = lambda f: all(flt(i[f]) >= flt(i.required_quantity) - 0.000001 for i in items)  # noqa: E731
    if done("consumed_quantity"): status = "Consumed"
    elif any(flt(i.consumed_quantity) > 0 for i in items): status = "Partially Consumed"
    elif done("tasked_quantity"): status = "Staged"  # fully tasked; single-order items also count as reserved once confirmed
    elif any(flt(i.tasked_quantity) > 0 for i in items): status = "Partially Staged"
    else: status = "Open"
    frappe.db.set_value("Production Material Request", pmr_name, "status", status)


def close_pmr(pmr_name):
    """Production is done with the order: what was staged for it and not consumed is released (to be moved back by an ordinary task)."""
    require_role("WMS Supervisor")
    frappe.db.set_value("Production Material Request", pmr_name, "status", "Closed")


# ------------------------------------------------------------------ staging (the app)

def _planned_out(product, bin_name, hu):
    """Quantity already tasked out of a stock line and not yet confirmed - not available to plan a second time."""
    rows = frappe.db.sql("""select coalesce(sum(planned_quantity - confirmed_quantity), 0) from `tabWarehouse Task` where docstatus=0 and status in ('Open', 'On Hold', 'Available', 'Assigned', 'In Process', 'Partially Confirmed')
        and product=%s and source_bin=%s and ifnull(source_hu, '')=%s""", (product, bin_name, hu or ""))
    return flt(rows[0][0])


def _source_lines(warehouse, product, psa, limit=5):
    """Stock lines the material could be staged from, oldest first: storage bins only, not the PSA itself, less what open tasks already take."""
    lines = _raw_source_lines(warehouse, product, psa, limit * 4)
    for l in lines: l["available_quantity"] = flt(l.available_quantity) - _planned_out(product, l.storage_bin, l.handling_unit)
    return [l for l in lines if l.available_quantity > 0.000001][:limit]


def _raw_source_lines(warehouse, product, psa, limit):
    return frappe.db.sql("""select b.storage_bin, b.handling_unit, b.batch_no, b.serial_no, b.available_quantity from `tabWMS Stock Balance` b
        join `tabStorage Bin` sb on sb.name = b.storage_bin join `tabStorage Type` st on st.name = sb.storage_type
        where b.warehouse=%s and b.product=%s and b.stock_type='AVAILABLE' and b.available_quantity>0 and b.storage_bin!=%s
        and sb.removal_blocked=0 and st.storage_role in ('Storage', '') order by b.first_receipt_date asc, b.name asc limit %s""",
        (warehouse, product, _supply_bin(psa), limit), as_dict=True)


def staging_overview(psa):
    """Open PMR items of a PSA with source proposals - the data behind the staging app."""
    require_role(*STAGE_ROLES)
    warehouse = frappe.db.get_value("Production Supply Area", psa, "warehouse")
    rows = frappe.db.sql("""select i.name as pmr_item, p.name as pmr, p.work_order, p.planned_date, i.product, i.operation, i.required_quantity, i.tasked_quantity,
        i.staged_quantity, i.consumed_quantity from `tabProduction Material Request Item` i join `tabProduction Material Request` p on p.name = i.parent
        where i.psa=%s and p.status in %s and i.required_quantity > i.tasked_quantity order by p.planned_date asc, p.creation asc, i.idx asc""", (psa, OPEN_STATUSES), as_dict=True)
    for r in rows:
        r["open_quantity"] = flt(r.required_quantity) - flt(r.tasked_quantity)
        r["proposals"] = _source_lines(warehouse, r.product, psa)
    return rows


def _create_staging_request(psa, product, source, quantity, reference_doctype, reference_name, reference_line):
    from frappe_wms.services.task import create_tasks_for_request
    warehouse, supply_bin = frappe.db.get_value("Production Supply Area", psa, ["warehouse", "supply_bin"])
    blank = lambda v: v or ["in", ["", None]]  # noqa: E731
    available = frappe.db.get_value("WMS Stock Balance", {"warehouse": warehouse, "product": product, "storage_bin": source["source_bin"], "handling_unit": blank(source.get("source_hu")),
        "batch_no": blank(source.get("batch_no")), "serial_no": blank(source.get("serial_no")), "stock_type": "AVAILABLE"}, "available_quantity", for_update=True)
    available = flt(available) - _planned_out(product, source["source_bin"], source.get("source_hu"))
    if available < quantity - 0.000001:
        frappe.throw(_("Only {0} of {1} is available in {2}").format(available, product, source["source_bin"]))
    request = frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Replenish", "warehouse": warehouse, "product": product, "requested_quantity": quantity,
        "stock_uom": frappe.db.get_value("Item", product, "stock_uom"), "source_bin": source["source_bin"], "source_hu": source.get("source_hu") or None,
        "batch_no": source.get("batch_no") or None, "serial_no": source.get("serial_no") or None, "destination_bin": supply_bin, "stock_type": "AVAILABLE",
        "reference_doctype": reference_doctype, "reference_name": reference_name, "reference_line": reference_line,
        "process_type": determine_process_type(warehouse, "Replenish", item=product, stock_type="AVAILABLE", default="REPLENISH"), "priority": "High", "status": "Open"})
    request.insert(ignore_permissions=True)
    return request.name, create_tasks_for_request(request.name)


def stage_items(psa, method, lines):
    """Create the staging tasks. lines: [{source_bin, source_hu?, batch_no?, serial_no?, quantity, pmr_item}] for "Single Order"
    (the stock is reserved to that PMR item); [{..., product, pmr_items: [...]}] for "Cross Order" (pooled, any open PMR of the product)."""
    require_role(*STAGE_ROLES)
    if method not in ("Single Order", "Cross Order"): frappe.throw(_("Unknown staging method {0}").format(method))
    created = []
    for line in lines:
        quantity = flt(line["quantity"])
        if quantity <= 0: frappe.throw(_("Quantity must be greater than zero"))
        names = [line["pmr_item"]] if method == "Single Order" else list(line["pmr_items"])
        items = [frappe.db.get_value("Production Material Request Item", n, ["name", "parent", "product", "psa", "required_quantity", "tasked_quantity"], as_dict=True, for_update=True) for n in names]
        if not items or any(not i for i in items): frappe.throw(_("Production Material Request item not found"))
        if {i.psa for i in items} != {psa} or len({i.product for i in items}) != 1: frappe.throw(_("All items must be for the same product and Production Supply Area {0}").format(psa))
        for parent in {i.parent for i in items}:
            if frappe.db.get_value("Production Material Request", parent, "status") not in OPEN_STATUSES: frappe.throw(_("{0} is not open").format(parent))
        if quantity > sum(flt(i.required_quantity) - flt(i.tasked_quantity) for i in items) + 0.000001: frappe.throw(_("More than is still open on the selected items"))
        if method == "Single Order":
            ref = ("Production Material Request", items[0].parent, items[0].name)
        else:
            ref = ("Production Supply Area", psa, None)
        request, task = _create_staging_request(psa, items[0].product, line, quantity, *ref)
        left = quantity
        for i in items:
            take = min(left, flt(i.required_quantity) - flt(i.tasked_quantity))
            if take > 0:
                frappe.db.sql("update `tabProduction Material Request Item` set tasked_quantity=tasked_quantity+%s where name=%s", (take, i.name))
                left -= take
        for parent in {i.parent for i in items}: _refresh_status(parent)
        created.append({"warehouse_request": request, "task": task})
    return created


def pmr_overview(psa):
    """The PSA's PMR items with their progress - the status half of the staging app."""
    require_role(*STAGE_ROLES)
    return frappe.db.sql("""select p.name as pmr, p.work_order, p.status, p.planned_date, i.product, i.operation, i.required_quantity, i.tasked_quantity,
        i.staged_quantity, i.consumed_quantity from `tabProduction Material Request Item` i join `tabProduction Material Request` p on p.name = i.parent
        where i.psa=%s and p.status in %s order by p.planned_date asc, p.creation asc, i.idx asc""", (psa, OPEN_STATUSES), as_dict=True)


def auto_stage(psa):
    """Single-order staging of every open item from the oldest stock - what an Automatic PSA does on its own."""
    require_role(*STAGE_ROLES)
    staged = []
    for r in staging_overview(psa):
        left, lines = r.open_quantity, []
        for src in _source_lines(frappe.db.get_value("Production Supply Area", psa, "warehouse"), r.product, psa, limit=50):
            take = min(left, flt(src.available_quantity))
            lines.append({"source_bin": src.storage_bin, "source_hu": src.handling_unit, "batch_no": src.batch_no, "serial_no": src.serial_no, "pmr_item": r.pmr_item, "quantity": take})
            left -= take
            if left <= 0.000001: break
        for line in lines:
            frappe.db.savepoint("wms_auto_stage")
            try:
                staged += stage_items(psa, "Single Order", [line])
            except frappe.ValidationError:
                frappe.db.rollback(save_point="wms_auto_stage")
                frappe.clear_messages()
    return staged


def run_auto_staging():
    """Scheduler: every Automatic PSA stages what is open (stock may have arrived since)."""
    for psa in frappe.get_all("Production Supply Area", filters={"active": 1, "staging_mode": "Automatic"}, pluck="name"):
        auto_stage(psa)
    frappe.db.commit()


def on_staging_confirmed(task, quantity):
    """A staging task put stock into the PSA: reserve it to its PMR item (single-order); cross-order stock stays in the pool."""
    if not task.warehouse_request: return
    ref = frappe.db.get_value("Warehouse Request", task.warehouse_request, ["reference_doctype", "reference_name", "reference_line"], as_dict=True)
    if ref and ref.reference_doctype == "Production Material Request" and ref.reference_line:
        frappe.db.sql("update `tabProduction Material Request Item` set staged_quantity=staged_quantity+%s where name=%s", (flt(quantity), ref.reference_line))


def on_staging_short(task, shortfall):
    """A staging task was closed short: that quantity is open for staging again."""
    if not task.warehouse_request or flt(shortfall) <= 0: return
    ref = frappe.db.get_value("Warehouse Request", task.warehouse_request, ["reference_doctype", "reference_name", "reference_line"], as_dict=True)
    if not ref: return
    if ref.reference_doctype == "Production Material Request" and ref.reference_line:
        frappe.db.sql("update `tabProduction Material Request Item` set tasked_quantity=greatest(tasked_quantity-%s, 0) where name=%s", (flt(shortfall), ref.reference_line))
        _refresh_status(ref.reference_name)
    elif ref.reference_doctype == "Production Supply Area":
        left = flt(shortfall)
        for i in frappe.db.sql("""select i.name, i.parent, i.tasked_quantity - i.staged_quantity as free from `tabProduction Material Request Item` i where i.psa=%s and i.product=%s
                and i.tasked_quantity > i.staged_quantity order by i.idx desc""", (ref.reference_name, task.product), as_dict=True):
            take = min(left, flt(i.free))
            frappe.db.sql("update `tabProduction Material Request Item` set tasked_quantity=tasked_quantity-%s where name=%s", (take, i.name))
            _refresh_status(i.parent)
            left -= take
            if left <= 0: break


# ------------------------------------------------------------------ consumption (backflush from ERPNext)

def _consume_line(pmr_name, product, quantity, reference_doctype, reference_name, key):
    pmr = frappe.get_doc("Production Material Request", pmr_name, for_update=True)
    items = [i for i in pmr.items if i.product == product and i.psa]
    if not items: frappe.throw(_("{0} is not on Production Material Request {1}").format(product, pmr_name))
    left = flt(quantity)
    for n, i in enumerate(items):
        want = left if n == len(items) - 1 else min(left, max(flt(i.required_quantity) - flt(i.consumed_quantity), 0))
        if want <= 0: continue
        own = max(flt(i.staged_quantity) - flt(i.consumed_quantity), 0)
        pool = pool_available(i.psa, product, lock=True)
        if want > own + pool + 0.000001:
            frappe.throw(_("Only {0} {1} is staged in {2} for {3}; consume less or stage more first").format(own + pool, product, i.psa, pmr_name))
        _book_out(i.psa, product, want, reference_doctype, reference_name, f"{key}:{i.name}")
        frappe.db.sql("update `tabProduction Material Request Item` set consumed_quantity=consumed_quantity+%s where name=%s", (want, i.name))
        left -= want
    _refresh_status(pmr_name)


def _book_out(psa, product, quantity, reference_doctype, reference_name, key):
    warehouse, supply_bin = frappe.db.get_value("Production Supply Area", psa, ["warehouse", "supply_bin"])
    balances = frappe.db.sql("""select handling_unit, batch_no, serial_no, stock_type, stock_uom, quantity from `tabWMS Stock Balance` where storage_bin=%s and product=%s and quantity>0
        order by first_receipt_date asc, name asc for update""", (supply_bin, product), as_dict=True)
    left, entries = flt(quantity), []
    for b in balances:
        take = min(left, flt(b.quantity))
        if b.serial_no: take = min(take, 1)
        if take <= 0: continue
        entries.append({"warehouse": warehouse, "product": product, "batch_no": b.batch_no, "serial_no": b.serial_no, "handling_unit": b.handling_unit, "storage_bin": supply_bin,
            "stock_type": b.stock_type, "quantity": -take, "stock_uom": b.stock_uom, "movement_type": "601"})
        left -= take
        if left <= 0: break
    if left > 0.000001: frappe.throw(_("Not enough {0} in {1}").format(product, supply_bin))
    for seq, e in enumerate(entries, 1): post_entries([e], reference_doctype, reference_name, f"{key}:{seq}")


def consume_from_stock_entry(se, method=None):
    """ERPNext booked the consumption of a Work Order's materials: book the same out of the PSA."""
    if se.get("purpose") not in CONSUMING_PURPOSES or not se.get("work_order"): return
    pmr = frappe.db.get_value("Production Material Request", {"work_order": se.work_order, "status": ["in", OPEN_STATUSES]})
    if not pmr: return
    for row in se.get("items"):
        if not row.s_warehouse or row.get("is_finished_item") or row.get("is_scrap_item") or not _wms_warehouse(row.s_warehouse): continue
        _consume_line(pmr, row.item_code, flt(row.transfer_qty) or flt(row.qty), "Stock Entry", se.name, f"PMRC:{se.name}:{row.name}")


def stock_entry_is_pmr_consumption(se):
    """True for a consumption entry of a Work Order with a PMR: its WMS-managed source warehouse is the PSA's, fed by staging."""
    return se.get("purpose") in CONSUMING_PURPOSES and bool(se.get("work_order")) and bool(
        frappe.db.exists("Production Material Request", {"work_order": se.work_order, "status": ["in", OPEN_STATUSES]}))
