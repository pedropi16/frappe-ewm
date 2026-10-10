"""Ad hoc warehouse tasks for many handling units / stock lines at once (SAP EWM /SCWM/ADHU, /SCWM/ADPROD).

The caller picks the lines with the Monitor's advanced selection; this creates one Internal Move task per
HU content line / stock line, all in one batch, so they form one warehouse order.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt

from frappe_wms.services.bin_rules import incoming_load, validate_destination_bin
from frappe_wms.services.determination import determine_process_type
from frappe_wms.services.stock import dim_values
from frappe_wms.services.warehouse_order import attach_task
from frappe_wms.utils import require_role

TASK_TYPES = {"Internal Move": "Internal Move", "Putaway": "Putaway"}  # activity of the chosen process type -> task type
BALANCE_FIELDS = ["name", "warehouse", "product", "batch_no", "serial_no", "storage_bin", "handling_unit", "stock_type", "quantity", "available_quantity", "stock_uom"]


def _balances(line, unpack=False):
    """(balance row, quantity) pairs a line stands for: an HU = everything in it, a stock line = its (free) quantity."""
    if line.get("handling_unit") and not line.get("name"):
        rows = frappe.get_all("WMS Stock Balance", filters={"handling_unit": line["handling_unit"], "quantity": [">", 0]}, fields=BALANCE_FIELDS)
        if not rows:
            frappe.throw(_("Handling Unit {0} holds no stock - a task needs something to move").format(line["handling_unit"]))
        return [(r, flt(r.quantity), not unpack) for r in rows]  # unpack: the stock lands loose ("No HU WT": product tasks out of the HU)
    bal = frappe.get_doc("WMS Stock Balance", line["name"]).as_dict()
    qty = flt(line.get("quantity")) or flt(bal.available_quantity)
    if qty <= 0 or qty > flt(bal.available_quantity) + 1e-6:
        frappe.throw(_("{0} in {1}: only {2} is free to move").format(bal.product, bal.storage_bin, flt(bal.available_quantity)))
    return [(bal, qty, False)]


def _destination(bal, qty, hu_type, pt, destination_bin, destination_hu, dtype, dsec, reserved):
    """The destination bin of a line: the one entered, the bin of the destination HU, the process type's fixed bin, or the one the bin determination picks
    (restricted to the storage type / section entered, if any) - then what was entered is checked against the bin."""
    if destination_bin: dest = destination_bin
    elif destination_hu and frappe.db.get_value("Handling Unit", destination_hu, "current_bin"): dest = frappe.db.get_value("Handling Unit", destination_hu, "current_bin")
    elif pt.default_destination_bin and not dtype and not dsec: dest = pt.default_destination_bin
    else:
        from frappe_wms.services.determination import determine_destination_bin
        iw, iv = incoming_load(bal.product, qty)
        dest = determine_destination_bin({"warehouse": bal.warehouse, "activity": pt.activity, "item": bal.product, "stock_type": bal.stock_type, "hu_type": hu_type,
            "source_storage_type": frappe.db.get_value("Storage Bin", bal.storage_bin, "storage_type"), "incoming_weight": iw, "incoming_volume": iv, "incoming_quantity": qty,
            "destination_hu": destination_hu or bal.handling_unit, "forced_storage_type": pt.default_destination_storage_type, "user_storage_type": dtype, "user_section": dsec, "exclude_bin": bal.storage_bin, "reserved_hu_counts": reserved})
        reserved[dest] = reserved.get(dest, 0) + 1
    info = frappe.db.get_value("Storage Bin", dest, ["warehouse", "storage_type", "storage_section"], as_dict=True)
    if not info: frappe.throw(_("Storage Bin {0} does not exist").format(dest))
    if dest == bal.storage_bin and not (bal.handling_unit and destination_hu): frappe.throw(_("Source and destination bins must differ"))
    if info.warehouse != bal.warehouse: frappe.throw(_("Destination bin {0} is not in warehouse {1}").format(dest, bal.warehouse))
    if dtype and info.storage_type != dtype: frappe.throw(_("Storage Bin {0} is in storage type {1}, not {2}").format(dest, info.storage_type, dtype))
    if dsec and info.storage_section != dsec: frappe.throw(_("Storage Bin {0} is in storage section {1}, not {2}").format(dest, info.storage_section, dsec))
    return dest


def _plan(lines, destination_bin=None, priority="Normal", process_type=None, reason=None, batch_key=None, destination_hu=None, unpack=0, dtype=None, dsec=None, dry=False):
    """One entry per task: the process type, the destination bin / storage type / section it resolves to. dry: only checks and resolves (the worklist's Enter); otherwise the tasks are inserted."""
    require_role("WMS Operator", "WMS Supervisor")
    if not lines: frappe.throw(_("Select at least one line"))
    chosen = frappe.get_cached_doc("Warehouse Process Type", process_type) if process_type else None
    if chosen and chosen.activity not in TASK_TYPES: frappe.throw(_("Process type {0} is not an ad hoc movement").format(process_type))
    batch_key = batch_key or frappe.generate_hash(length=10)
    plans, checked, reserved, hu_dest = [], set(), {}, {}  # checked: HUs already cleared of open tasks (an HU holding several products gets several tasks)
    for line in lines:
        for bal, qty, whole_hu in _balances(line, cint(unpack)):
            if not bal.storage_bin: frappe.throw(_("{0} is not in a bin").format(bal.product))
            hu = bal.handling_unit
            if hu and hu not in checked and frappe.db.exists("Warehouse Task", {"source_hu": hu, "docstatus": 0, "status": ["not in", ["Cancelled", "Confirmed"]]}):
                frappe.throw(_("{0} already has an open warehouse task").format(hu))
            checked.add(hu)
            # moving all an HU holds moves the HU; part of it lands loose (same rule as create_and_confirm_move)
            whole = hu and not whole_hu and frappe.db.sql("select count(*), sum(quantity) from `tabWMS Stock Balance` where handling_unit=%s and quantity>0", hu)[0]
            moves_hu = bool(hu) and not cint(unpack) and (whole_hu or (whole[0] == 1 and qty >= flt(whole[1]) - 1e-6))
            hu_type = frappe.db.get_value("Handling Unit", hu, "hu_type") if hu else None
            pt = chosen or frappe.get_cached_doc("Warehouse Process Type", determine_process_type(bal.warehouse, "Internal Move", item=bal.product, stock_type=bal.stock_type, default="INTERNAL_MOVE"))
            # a whole HU goes to one bin, whatever number of product lines it holds
            dest = hu_dest[hu] if moves_hu and hu in hu_dest else _destination(bal, qty, hu_type, pt, destination_bin, destination_hu, dtype, dsec, reserved)
            if moves_hu: hu_dest[hu] = dest
            iw, iv = incoming_load(bal.product, qty)
            validate_destination_bin(dest, item=bal.product, incoming_quantity=qty, stock_type=bal.stock_type, hu_type=hu_type,
                                     batch_no=bal.batch_no, destination_hu=destination_hu or (hu if moves_hu else None), incoming_weight=iw, incoming_volume=iv)
            info = frappe.db.get_value("Storage Bin", dest, ["storage_type", "storage_section"], as_dict=True)
            plan = {"task": None, "product": bal.product, "quantity": qty, "process_type": pt.name, "process_type_name": pt.process_type_name, "destination_bin": dest,
                    "destination_storage_type": info.storage_type, "destination_section": info.storage_section}
            if not dry:
                task = frappe.get_doc({
                    "doctype": "Warehouse Task", "task_type": TASK_TYPES[pt.activity], "warehouse": bal.warehouse, "product": bal.product, "planned_quantity": qty,
                    "stock_uom": bal.stock_uom, "batch_no": bal.batch_no, "serial_no": bal.serial_no, "source_bin": bal.storage_bin, "source_hu": hu,
                    "destination_bin": dest, "destination_hu": destination_hu, "unpack_at_destination": 1 if hu and not moves_hu and not destination_hu else 0, "stock_type_from": bal.stock_type, "stock_type_to": bal.stock_type,
                    **dim_values(bal), "movement_type": pt.movement_type, "priority": priority or "Normal", "reason": reason, "status": "Open"})
                attach_task(task, batch_key, default_queue=pt.default_queue)
                task.insert(ignore_permissions=True)
                plan["task"] = task.name
            plans.append(plan)
    return plans


def create_adhoc_tasks(lines, destination_bin=None, priority="Normal", process_type=None, reason=None, confirm=0, batch_key=None, destination_hu=None, unpack=0, dtype=None, dsec=None):
    return _create(lines, destination_bin, priority, process_type, reason, confirm, batch_key, destination_hu, unpack, dtype, dsec)[0]


def _create(lines, destination_bin, priority, process_type, reason, confirm, batch_key, destination_hu, unpack, dtype, dsec):
    plans = _plan(lines, destination_bin, priority, process_type, reason, batch_key, destination_hu, unpack, dtype, dsec)
    created = [p["task"] for p in plans]
    if cint(confirm):  # immediate confirmation: the stock moves now, the tasks are done
        from frappe_wms.services.task import _UNPACK, confirm_task
        for name in created: confirm_task(name, verify=False, destination_hu=_UNPACK if frappe.db.get_value("Warehouse Task", name, "unpack_at_destination") else None)
    return created, plans


def check_lines(lines, defaults=None):
    """The worklist's Enter: resolves every line (process type, destination bin with its storage type and section) and says what would be refused, creating nothing."""
    from frappe_wms.services.locks import require_free_many
    from frappe_wms.services.packing_center import _fail_text
    defaults, out = defaults or {}, []
    for i, line in enumerate(lines):
        savepoint = f"adhoc_chk_{i}"
        frappe.db.savepoint(savepoint)
        try:
            require_free_many([_lock_key(line)])
            plans = _plan([_core(line)], line.get("destination_bin") or defaults.get("destination_bin"), "Normal", line.get("process_type") or defaults.get("process_type"), None, None,
                          line.get("destination_hu"), line.get("unpack"), line.get("destination_storage_type"), line.get("destination_section"), dry=True)
            out.append({"line": i, "ok": 1, **{k: plans[0][k] for k in ("process_type", "process_type_name", "destination_bin", "destination_storage_type", "destination_section")}, "destinations": len({p["destination_bin"] for p in plans})})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            out.append({"line": i, "ok": 0, "error": _fail_text(e)})
        finally:
            frappe.db.rollback(save_point=savepoint)
    return out


def _core(line):
    return {"handling_unit": line["handling_unit"]} if line.get("handling_unit") and not line.get("name") else {"name": line["name"], "quantity": line.get("quantity")}


def _lock_key(line):
    return ("Handling Unit", line["handling_unit"]) if line.get("handling_unit") and not line.get("name") else ("WMS Stock Balance", line["name"])


def process_lines(lines, defaults=None):
    """The worklist screen's Create: every line has its own destination / process type / reason / confirmation (falling back to `defaults`) and goes through on its own,
    so a locked or refused line does not stop the others. A line without a destination gets the one the process type / bin determination picks.
    -> {"created": [{"line": i, "tasks": [...], "destination_bin", "destination_storage_type", "destination_section", "process_type"}], "errors": [{"line": i, "error": text}]}"""
    from frappe_wms.services.locks import require_free_many
    from frappe_wms.services.packing_center import _fail_text
    defaults, batch_key, out = defaults or {}, frappe.generate_hash(length=10), {"created": [], "errors": []}
    for i, line in enumerate(lines):
        savepoint = f"adhoc_{i}"
        frappe.db.savepoint(savepoint)
        try:
            require_free_many([_lock_key(line)])
            pick = lambda k, fallback=None: line.get(k) or defaults.get(k) or fallback
            tasks, plans = _create([_core(line)], pick("destination_bin"), pick("priority", "Normal"), pick("process_type"), pick("reason"), line.get("confirm", defaults.get("confirm", 0)), batch_key,
                                   line.get("destination_hu"), line.get("unpack"), line.get("destination_storage_type"), line.get("destination_section"))
            first = plans[0]
            out["created"].append({"line": i, "tasks": tasks, **{k: first[k] for k in ("process_type", "process_type_name", "destination_bin", "destination_storage_type", "destination_section")}})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            frappe.db.rollback(save_point=savepoint)
            out["errors"].append({"line": i, "error": _fail_text(e)})
    return out


def _like(value):
    value = (value or "").strip().replace("*", "%")
    return value if "%" in value else f"%{value}%"


def find_rows(warehouse, mode, by="handling_unit", value=None, names=None, limit=200):
    """Worklist rows of the ad hoc screens: handling units (mode "hu") or stock lines (mode "stock") of a warehouse; `names` re-reads known ones."""
    require_role("WMS Operator", "WMS Supervisor", "WMS Inventory Controller")
    if mode == "hu":
        cond, args = ["h.warehouse = %(wh)s", "h.current_bin is not null", "h.stock_status != 'Empty'"], {"wh": warehouse, "limit": limit}
        if names: cond.append("h.name in %(names)s"); args["names"] = tuple(names)
        elif by == "storage_bin": cond.append("h.current_bin like %(v)s"); args["v"] = _like(value)
        elif by == "product": cond.append("h.name in (select handling_unit from `tabWMS Stock Balance` where quantity > 0 and product like %(v)s)"); args["v"] = _like(value)
        else: cond.append("(h.name like %(v)s or h.hu_number like %(v)s)"); args["v"] = _like(value)
        rows = frappe.db.sql(f"""select h.name as handling_unit, h.hu_number, h.hu_type, h.top_hu, h.parent_hu, h.status, h.current_bin as source_bin, b.storage_type, b.storage_section,
            (select count(*) from `tabWarehouse Task` t where t.source_hu = h.name and t.docstatus = 0 and t.status not in ('Cancelled', 'Confirmed')) as open_wt
            from `tabHandling Unit` h left join `tabStorage Bin` b on b.name = h.current_bin where {' and '.join(cond)} order by h.current_bin, h.name limit %(limit)s""", args, as_dict=True)
        return [{**r, "key": r.handling_unit, "top_hu": r.top_hu or r.handling_unit} for r in rows]
    cond, args = ["s.warehouse = %(wh)s", "s.quantity > 0", "s.available_quantity > 0", "s.storage_bin is not null"], {"wh": warehouse, "limit": limit}
    if names: cond.append("s.name in %(names)s"); args["names"] = tuple(names)
    elif by == "storage_bin": cond.append("s.storage_bin like %(v)s"); args["v"] = _like(value)
    elif by == "handling_unit": cond.append("(s.handling_unit like %(v)s or s.handling_unit in (select name from `tabHandling Unit` where top_hu like %(v)s))"); args["v"] = _like(value)  # nested HUs: their content too
    else: cond.append("s.product like %(v)s"); args["v"] = _like(value)
    rows = frappe.db.sql(f"""select s.name, s.product, s.batch_no, s.serial_no, s.stock_type, s.handling_unit, s.storage_bin as source_bin, b.storage_type, b.storage_section,
        s.available_quantity as available, s.stock_uom from `tabWMS Stock Balance` s left join `tabStorage Bin` b on b.name = s.storage_bin
        where {' and '.join(cond)} order by s.storage_bin, s.product limit %(limit)s""", args, as_dict=True)
    return [{**r, "key": r.name, "quantity": r.available} for r in rows]


def hu_content(handling_unit):
    require_role("WMS Operator", "WMS Supervisor", "WMS Inventory Controller")
    return frappe.get_all("WMS Stock Balance", filters={"handling_unit": handling_unit, "quantity": [">", 0]}, fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "allocated_quantity", "stock_uom"], order_by="product asc")


def hu_master(handling_unit):
    require_role("WMS Operator", "WMS Supervisor", "WMS Inventory Controller")
    return frappe.db.get_value("Handling Unit", handling_unit, ["hu_type", "packaging_material", "status", "stock_status", "current_bin", "parent_hu", "top_hu", "gross_weight", "net_weight", "tare_weight", "volume", "outbound_delivery", "shipment", "closed", "loaded", "sscc", "external_reference"], as_dict=True)


def task_status(names):
    require_role("WMS Operator", "WMS Supervisor", "WMS Inventory Controller")
    return frappe.get_all("Warehouse Task", filters={"name": ["in", names or [""]]}, fields=["name", "task_type", "status", "product", "planned_quantity", "confirmed_quantity", "source_bin", "destination_bin", "source_hu", "warehouse_order", "reason"], order_by="creation asc")
