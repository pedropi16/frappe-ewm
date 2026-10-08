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
        cycle = control_cycle(items[-1]["psa"], row.item_code) if items[-1]["psa"] else None
        if cycle and cycle.staging_method in ("Crate Parts", "Direct Consumption"): items[-1]["tasked_quantity"] = items[-1]["required_quantity"]  # nothing to stage per order
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


def psa_bins(psa):
    """Every bin of a PSA: the default supply bin first, then its further bins."""
    bins = [_supply_bin(psa)] + frappe.get_all("PSA Bin", filters={"parent": psa, "parenttype": "Production Supply Area"}, pluck="storage_bin", order_by="idx asc")
    return list(dict.fromkeys(b for b in bins if b))


def control_cycle(psa, product):
    return frappe.db.get_value("Production Supply Control Cycle", {"production_supply_area": psa, "product": product, "active": 1},
        ["name", "staging_method", "staging_bin", "minimum_quantity", "maximum_quantity"], as_dict=True)


def staging_bin(psa, product):
    """Where a material is staged in its PSA: its control cycle's bin, else the PSA's supply bin."""
    cycle = control_cycle(psa, product)
    return (cycle and cycle.staging_bin) or _supply_bin(psa)


def _deco_locations(psa):
    work_center = frappe.db.get_value("Production Supply Area", psa, "deconsolidation_work_center")
    return frappe.get_all("Work Center Location", filters={"parent": work_center, "parenttype": "Work Center"}, pluck="storage_bin", order_by="idx asc") if work_center else []


def _psa_stock(psa, product, lock=False):
    rows = frappe.db.sql("select quantity from `tabWMS Stock Balance` where storage_bin in %s and product=%s and quantity>0" + (" for update" if lock else ""), (tuple(psa_bins(psa)), product))
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
        where b.warehouse=%s and b.product=%s and b.stock_type='AVAILABLE' and b.available_quantity>0 and b.storage_bin not in %s and ifnull(b.stock_owner, '') = '' and ifnull(b.entitled_party, '') = '' and ifnull(b.special_stock_ref, '') = ''
        and sb.removal_blocked=0 and st.storage_role in ('Storage', '') order by b.first_receipt_date asc, b.name asc limit %s""",
        (warehouse, product, tuple(psa_bins(psa) + _deco_locations(psa)) or ("",), limit), as_dict=True)


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
    warehouse = frappe.db.get_value("Production Supply Area", psa, "warehouse")
    supply_bin = staging_bin(psa, product)
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
    task = create_tasks_for_request(request.name)
    if reference_doctype == "Production Material Request": _via_deconsolidation(psa, reference_name, task)
    return request.name, task


def _free_location(psa, pmr):
    """The order's location in the deconsolidation work center - assigned once, a free one (no stock, no open task, not another open order's)."""
    current = frappe.db.get_value("Production Material Request", pmr, "deconsolidation_bin")
    if current: return current
    taken = set(frappe.get_all("Production Material Request", filters={"status": ["in", OPEN_STATUSES], "deconsolidation_bin": ["is", "set"]}, pluck="deconsolidation_bin"))
    for loc in _deco_locations(psa):
        if loc in taken or not frappe.db.get_value("Storage Bin", loc, "active"): continue
        if frappe.db.exists("WMS Stock Balance", {"storage_bin": loc, "quantity": [">", 0]}) or frappe.db.exists("Warehouse Task", {"destination_bin": loc, "docstatus": 0, "status": ["!=", "Cancelled"]}): continue
        frappe.db.set_value("Production Material Request", pmr, "deconsolidation_bin", loc)
        return loc
    return None


def _via_deconsolidation(psa, pmr, tasks):
    """PSA with a deconsolidation work center: the first leg goes to the order's location there; the leg on to the PSA is created when it is confirmed
    (the layout storage control mechanism, services/layout_control)."""
    if not frappe.db.get_value("Production Supply Area", psa, "deconsolidation_work_center"): return
    location = _free_location(psa, pmr)
    if not location: frappe.throw(_("No free location in the deconsolidation work center of {0}").format(psa))
    for task in ([tasks] if isinstance(tasks, str) else tasks):
        final = frappe.db.get_value("Warehouse Task", task, "destination_bin")
        if final != location: frappe.db.set_value("Warehouse Task", task, {"destination_bin": location, "final_destination_bin": final})


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
    return frappe.db.sql("""select i.name as pmr_item, p.name as pmr, p.work_order, p.status, p.planned_date, i.product, i.operation, i.required_quantity, i.tasked_quantity,
        i.staged_quantity, i.consumed_quantity from `tabProduction Material Request Item` i join `tabProduction Material Request` p on p.name = i.parent
        where i.psa=%s and p.status in %s order by p.planned_date asc, p.creation asc, i.idx asc""", (psa, OPEN_STATUSES), as_dict=True)


def _source_plan(psa, product, quantity):
    """Source stock lines (oldest first) covering up to quantity: [(line, take)]."""
    plan, left = [], quantity
    for src in _source_lines(frappe.db.get_value("Production Supply Area", psa, "warehouse"), product, psa, limit=50):
        take = min(left, flt(src.available_quantity))
        plan.append(({"source_bin": src.storage_bin, "source_hu": src.handling_unit, "batch_no": src.batch_no, "serial_no": src.serial_no}, take))
        left -= take
        if left <= 0.000001: break
    return plan


def _try_stage(psa, method, line):
    frappe.db.savepoint("wms_auto_stage")
    try:
        return stage_items(psa, method, [line])
    except frappe.ValidationError:
        frappe.db.rollback(save_point="wms_auto_stage")
        frappe.clear_messages()
        return []


def auto_stage(psa):
    """Stage every open item by its control cycle: Pick Parts (the default) per order from the oldest stock, Release Order Parts as one
    pooled movement per product over all the open orders. Crate Parts and Direct Consumption need no staging per order."""
    require_role(*STAGE_ROLES)
    staged, aggregated = [], {}
    for r in staging_overview(psa):
        cycle = control_cycle(psa, r.product)
        if cycle and cycle.staging_method == "Release Order Parts":
            aggregated.setdefault(r.product, []).append(r)
            continue
        for src, take in _source_plan(psa, r.product, r.open_quantity):
            staged += _try_stage(psa, "Single Order", {**src, "pmr_item": r.pmr_item, "quantity": take})
    for product, rows in aggregated.items():
        for src, take in _source_plan(psa, product, sum(r.open_quantity for r in rows)):
            staged += _try_stage(psa, "Cross Order", {**src, "product": product, "pmr_items": [r.pmr_item for r in rows], "quantity": take})
    return staged


def check_crate_parts(psa=None, product=None):
    """Crate Parts: keep the material of a control cycle between its minimum and maximum in its PSA bin, independent of any order."""
    filters = {"active": 1, "staging_method": "Crate Parts", **({"production_supply_area": psa} if psa else {}), **({"product": product} if product else {})}
    created = []
    for c in frappe.get_all("Production Supply Control Cycle", filters=filters, fields=["production_supply_area", "product", "staging_bin", "minimum_quantity", "maximum_quantity"]):
        target = c.staging_bin or _supply_bin(c.production_supply_area)
        held = flt(frappe.db.sql("select coalesce(sum(quantity), 0) from `tabWMS Stock Balance` where storage_bin=%s and product=%s and quantity>0", (target, c.product))[0][0])
        coming = flt(frappe.db.sql("""select coalesce(sum(planned_quantity - confirmed_quantity), 0) from `tabWarehouse Task` where docstatus=0 and destination_bin=%s and product=%s
            and status in ('Open', 'On Hold', 'Available', 'Assigned', 'In Process', 'Partially Confirmed')""", (target, c.product))[0][0])
        if held + coming >= flt(c.minimum_quantity): continue
        for src, take in _source_plan(c.production_supply_area, c.product, flt(c.maximum_quantity) - held - coming):
            frappe.db.savepoint("wms_crate")
            try:
                request, task = _create_staging_request(c.production_supply_area, c.product, src, take, "Production Supply Control Cycle", f"{c.production_supply_area}-{c.product}", None)
                created.append({"warehouse_request": request, "task": task})
            except frappe.ValidationError:
                frappe.db.rollback(save_point="wms_crate")
                frappe.clear_messages()
    return created


def return_unused(pmr_item):
    """Material staged for an order and not consumed goes back to storage (tasks from the PSA bins, destination by the usual putaway
    determination); the reservation is released as each task is confirmed."""
    require_role(*STAGE_ROLES)
    from frappe_wms.services.task import create_tasks_for_request
    item = frappe.db.get_value("Production Material Request Item", pmr_item, ["name", "parent", "product", "psa", "staged_quantity", "consumed_quantity"], as_dict=True, for_update=True)
    if not item: frappe.throw(_("Production Material Request item not found"))
    left = flt(item.staged_quantity) - flt(item.consumed_quantity)
    if left <= 0.000001: frappe.throw(_("Nothing staged and unconsumed for this item"))
    warehouse = frappe.db.get_value("Production Supply Area", item.psa, "warehouse")
    balances = frappe.db.sql("""select storage_bin, handling_unit, batch_no, serial_no, quantity, stock_uom from `tabWMS Stock Balance` where storage_bin in %s and product=%s and quantity>0
        order by first_receipt_date desc, name desc""", (tuple(psa_bins(item.psa)), item.product), as_dict=True)  # the newest first: the oldest stays for production
    created = []
    for b in balances:
        take = min(left, flt(b.quantity))
        if take <= 0: continue
        request = frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Putaway", "warehouse": warehouse, "product": item.product, "requested_quantity": take, "stock_uom": b.stock_uom,
            "source_bin": b.storage_bin, "source_hu": b.handling_unit or None, "batch_no": b.batch_no or None, "serial_no": b.serial_no or None, "stock_type": "AVAILABLE",
            "reference_doctype": "Production Material Request", "reference_name": item.parent, "reference_line": item.name,
            "process_type": determine_process_type(warehouse, "Putaway", item=item.product, stock_type="AVAILABLE", default="GR_PUTAWAY"), "priority": "Normal", "status": "Open"})
        request.insert(ignore_permissions=True)
        created.append({"warehouse_request": request.name, "task": create_tasks_for_request(request.name)})
        left -= take
        if left <= 0.000001: break
    return created


def run_auto_staging():
    """Scheduler: every Automatic PSA stages what is open (stock may have arrived since)."""
    for psa in frappe.get_all("Production Supply Area", filters={"active": 1, "staging_mode": "Automatic"}, pluck="name"):
        auto_stage(psa)
    check_crate_parts()
    frappe.db.commit()


def on_staging_confirmed(task, quantity):
    """A staging task put stock into the PSA: reserve it to its PMR item (single-order); cross-order stock stays in the pool."""
    if not task.warehouse_request: return
    ref = frappe.db.get_value("Warehouse Request", task.warehouse_request, ["reference_doctype", "reference_name", "reference_line", "request_type"], as_dict=True)
    if ref and ref.reference_doctype == "Production Material Request" and ref.reference_line and ref.request_type == "Putaway":
        # material returned from the PSA to storage: no longer reserved to the order
        frappe.db.sql("update `tabProduction Material Request Item` set staged_quantity=greatest(staged_quantity-%s, 0) where name=%s", (flt(quantity), ref.reference_line))
        return
    if ref and ref.reference_doctype == "Production Material Request" and ref.reference_line:
        psa = frappe.db.get_value("Production Material Request Item", ref.reference_line, "psa")
        if not psa or task.destination_bin not in psa_bins(psa): return  # a first leg to an intermediate bin (layout storage control): staged when it reaches the PSA
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
        cycle = control_cycle(i.psa, product)
        direct = bool(cycle and cycle.staging_method == "Direct Consumption")  # never staged: taken from where it is stored
        if not direct:
            own = max(flt(i.staged_quantity) - flt(i.consumed_quantity), 0)
            pool = pool_available(i.psa, product, lock=True)
            if want > own + pool + 0.000001:
                frappe.throw(_("Only {0} {1} is staged in {2} for {3}; consume less or stage more first").format(own + pool, product, i.psa, pmr_name))
        _book_out(i.psa, product, want, reference_doctype, reference_name, f"{key}:{i.name}", direct=direct)
        frappe.db.sql("update `tabProduction Material Request Item` set consumed_quantity=consumed_quantity+%s where name=%s", (want, i.name))
        left -= want
    _refresh_status(pmr_name)
    for psa in {i.psa for i in items}: check_crate_parts(psa, product)  # consumption may have emptied a crate-parts bin


def _book_out(psa, product, quantity, reference_doctype, reference_name, key, direct=False):
    """Book quantity out of the PSA's bins - or, for Direct Consumption, out of the storage bins the material sits in (oldest first)."""
    warehouse = frappe.db.get_value("Production Supply Area", psa, "warehouse")
    bins = tuple(psa_bins(psa))
    if direct:
        balances = frappe.db.sql("""select b.storage_bin, b.handling_unit, b.batch_no, b.serial_no, b.stock_type, b.stock_uom, b.available_quantity as quantity from `tabWMS Stock Balance` b
            join `tabStorage Bin` sb on sb.name = b.storage_bin join `tabStorage Type` st on st.name = sb.storage_type where b.warehouse=%s and b.product=%s and b.stock_type='AVAILABLE'
            and b.available_quantity>0 and b.storage_bin not in %s and ifnull(b.stock_owner, '') = '' and ifnull(b.entitled_party, '') = '' and ifnull(b.special_stock_ref, '') = '' and sb.removal_blocked=0 and st.storage_role in ('Storage', '') order by b.first_receipt_date asc, b.name asc for update""",
            (warehouse, product, bins), as_dict=True)
    else:
        balances = frappe.db.sql("""select storage_bin, handling_unit, batch_no, serial_no, stock_type, stock_uom, quantity from `tabWMS Stock Balance` where storage_bin in %s and product=%s and quantity>0
            order by first_receipt_date asc, name asc for update""", (bins, product), as_dict=True)
    left, entries = flt(quantity), []
    for b in balances:
        take = min(left, flt(b.quantity))
        if b.serial_no: take = min(take, 1)
        if take <= 0: continue
        entries.append({"warehouse": warehouse, "product": product, "batch_no": b.batch_no, "serial_no": b.serial_no, "handling_unit": b.handling_unit, "storage_bin": b.storage_bin,
            "stock_type": b.stock_type, "quantity": -take, "stock_uom": b.stock_uom, "movement_type": "601"})
        left -= take
        if left <= 0: break
    if left > 0.000001: frappe.throw(_("Not enough {0} in {1}").format(product, ", ".join(bins) if not direct else warehouse))
    for seq, e in enumerate(entries, 1): post_entries([e], reference_doctype, reference_name, f"{key}:{seq}")


def consume_from_stock_entry(se, method=None):
    """ERPNext booked the consumption of a Work Order's materials: book the same out of the PSA."""
    if se.get("purpose") not in CONSUMING_PURPOSES or not se.get("work_order"): return
    pmr = frappe.db.get_value("Production Material Request", {"work_order": se.work_order, "status": ["in", OPEN_STATUSES]})
    if not pmr: return
    for row in se.get("items"):
        if not row.s_warehouse or row.get("is_finished_item") or row.get("is_scrap_item") or not _wms_warehouse(row.s_warehouse): continue
        _consume_line(pmr, row.item_code, flt(row.transfer_qty) or flt(row.qty), "Stock Entry", se.name, f"PMRC:{se.name}:{row.name}")


def reverse_consumption(se, method=None):
    """The consumption entry was cancelled in ERPNext: put what it booked out of the PSA back, and the PMR items' consumed quantity with it."""
    entries = frappe.get_all("WMS Stock Ledger Entry", filters={"reference_doctype": "Stock Entry", "reference_name": se.name, "idempotency_key": ["like", "PMRC:%"],
        "reversal_of": ["in", [None, ""]]}, fields=["*"], order_by="creation asc")
    pmrs = set()
    for n, e in enumerate(entries, 1):
        values = {k: e.get(k) for k in ("warehouse", "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type", "stock_uom")}
        values.update({"quantity": -flt(e.quantity), "movement_type": "602", "reversal_of": e.name})
        post_entries([values], "Stock Entry", se.name, f"PMRC-REV:{e.name}")
        item = e.idempotency_key.split(":")[3]  # PMRC:<entry>:<row>:<pmr item>:<seq>:<n>
        frappe.db.sql("update `tabProduction Material Request Item` set consumed_quantity=greatest(consumed_quantity-%s, 0) where name=%s", (-flt(e.quantity), item))
        pmrs.add(frappe.db.get_value("Production Material Request Item", item, "parent"))
    for pmr in pmrs: _refresh_status(pmr)


def stock_entry_is_pmr_consumption(se):
    """True for a consumption entry of a Work Order with a PMR: its WMS-managed source warehouse is the PSA's, fed by staging."""
    return se.get("purpose") in CONSUMING_PURPOSES and bool(se.get("work_order")) and bool(
        frappe.db.exists("Production Material Request", {"work_order": se.work_order, "status": ["in", OPEN_STATUSES]}))
