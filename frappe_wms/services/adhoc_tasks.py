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


def create_adhoc_tasks(lines, destination_bin, priority="Normal", process_type=None, reason=None, confirm=0, batch_key=None, destination_hu=None, unpack=0):
    require_role("WMS Operator", "WMS Supervisor")
    if not lines: frappe.throw(_("Select at least one line"))
    if not destination_bin: frappe.throw(_("Enter the destination bin"))
    chosen = frappe.get_cached_doc("Warehouse Process Type", process_type) if process_type else None
    if chosen and chosen.activity not in TASK_TYPES: frappe.throw(_("Process type {0} is not an ad hoc movement").format(process_type))
    destination = frappe.get_doc("Storage Bin", destination_bin)
    batch_key = batch_key or frappe.generate_hash(length=10)
    created, checked = [], set()  # checked: HUs already cleared of open tasks (an HU holding several products gets several tasks)
    for line in lines:
        for bal, qty, whole_hu in _balances(line, cint(unpack)):
            if destination.warehouse != bal.warehouse: frappe.throw(_("Destination bin {0} is not in warehouse {1}").format(destination_bin, bal.warehouse))
            if not bal.storage_bin: frappe.throw(_("{0} is not in a bin").format(bal.product))
            hu = bal.handling_unit
            if hu and hu not in checked and frappe.db.exists("Warehouse Task", {"source_hu": hu, "docstatus": 0, "status": ["not in", ["Cancelled", "Confirmed"]]}):
                frappe.throw(_("{0} already has an open warehouse task").format(hu))
            checked.add(hu)
            # moving all an HU holds moves the HU; part of it lands loose (same rule as create_and_confirm_move)
            whole = hu and not whole_hu and frappe.db.sql("select count(*), sum(quantity) from `tabWMS Stock Balance` where handling_unit=%s and quantity>0", hu)[0]
            moves_hu = bool(hu) and not cint(unpack) and (whole_hu or (whole[0] == 1 and qty >= flt(whole[1]) - 1e-6))
            hu_type = frappe.db.get_value("Handling Unit", hu, "hu_type") if hu else None
            iw, iv = incoming_load(bal.product, qty)
            validate_destination_bin(destination_bin, item=bal.product, incoming_quantity=qty, stock_type=bal.stock_type, hu_type=hu_type,
                                     batch_no=bal.batch_no, destination_hu=destination_hu or (hu if moves_hu else None), incoming_weight=iw, incoming_volume=iv)
            pt = chosen or frappe.get_cached_doc("Warehouse Process Type", determine_process_type(bal.warehouse, "Internal Move", item=bal.product, stock_type=bal.stock_type, default="INTERNAL_MOVE"))
            task = frappe.get_doc({
                "doctype": "Warehouse Task", "task_type": TASK_TYPES[pt.activity], "warehouse": bal.warehouse, "product": bal.product, "planned_quantity": qty,
                "stock_uom": bal.stock_uom, "batch_no": bal.batch_no, "serial_no": bal.serial_no, "source_bin": bal.storage_bin, "source_hu": hu,
                "destination_bin": destination_bin, "destination_hu": destination_hu, "unpack_at_destination": 1 if hu and not moves_hu and not destination_hu else 0, "stock_type_from": bal.stock_type, "stock_type_to": bal.stock_type,
                **dim_values(bal), "movement_type": pt.movement_type, "priority": priority or "Normal", "reason": reason, "status": "Open"})
            attach_task(task, batch_key, default_queue=pt.default_queue)
            task.insert(ignore_permissions=True)
            created.append(task.name)
    if cint(confirm):  # immediate confirmation: the stock moves now, the tasks are done
        from frappe_wms.services.task import _UNPACK, confirm_task
        for name in created: confirm_task(name, verify=False, destination_hu=_UNPACK if frappe.db.get_value("Warehouse Task", name, "unpack_at_destination") else None)
    return created


def process_lines(lines, defaults=None):
    """The worklist screen's Create: every line has its own destination / process type / reason / confirmation (falling back to `defaults`) and goes through on its own,
    so a locked or refused line does not stop the others. -> {"created": [{"line": i, "tasks": [...]}], "errors": [{"line": i, "error": text}]}"""
    from frappe_wms.services.locks import require_free_many
    defaults, batch_key, out = defaults or {}, frappe.generate_hash(length=10), {"created": [], "errors": []}
    for i, line in enumerate(lines):
        savepoint = f"adhoc_{i}"
        frappe.db.savepoint(savepoint)
        try:
            core = {"handling_unit": line["handling_unit"]} if line.get("handling_unit") and not line.get("name") else {"name": line["name"], "quantity": line.get("quantity")}
            require_free_many([("Handling Unit", core["handling_unit"]) if "handling_unit" in core else ("WMS Stock Balance", core["name"])])
            pick = lambda k, fallback=None: line.get(k) or defaults.get(k) or fallback
            tasks = create_adhoc_tasks([core], pick("destination_bin"), pick("priority", "Normal"), pick("process_type"), pick("reason"), line.get("confirm", defaults.get("confirm", 0)), batch_key, line.get("destination_hu"), line.get("unpack"))
            out["created"].append({"line": i, "tasks": tasks})
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            frappe.db.rollback(save_point=savepoint)
            from frappe_wms.services.packing_center import _fail_text
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
    elif by == "handling_unit": cond.append("s.handling_unit like %(v)s"); args["v"] = _like(value)
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
