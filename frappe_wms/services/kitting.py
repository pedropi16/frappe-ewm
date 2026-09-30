"""Kitting (SAP EWM kit-to-stock / reverse kitting) at a kitting work center bin.

  create        a Kitting Order from the kit item's BOM
  stage         warehouse tasks bring the inputs (components, or the kit when disassembling)
                from storage to the work center bin - automatically on creation or from the RF
                screen, per WMS Warehouse > Kitting > Stage Components
  complete      consumes the inputs actually lying at the work center (every batch/HU row, FEFO)
                and posts the output there, optionally onto a Handling Unit
  put away      the output gets putaway tasks from the work center when the order (default: the
                warehouse's Put Away Kitting Output) says so
  cancel        open staging tasks are cancelled; staged stock stays at the work center
"""
import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

def list_open_kitting_orders(user=None):
    # Mirrors list_open_vas_orders: an RF operator only sees orders in their own resource's
    # warehouse, matching the RF app's own scoping convention for floor work lists.
    require_role("WMS Operator", "WMS Supervisor")
    resource = my_resource(user)
    filters = {"status": ["in", ("Draft", "Open")]}
    if resource: filters["warehouse"] = resource.warehouse
    return frappe.get_list("Kitting Order", filters=filters,
        fields=["name", "kit_item", "bom", "warehouse", "work_center_bin", "quantity", "direction", "status", "creation"],
        order_by="creation asc", limit=50)

def _settings(warehouse):
    return frappe.db.get_value("WMS Warehouse", warehouse, ["kitting_stage_components", "kitting_putaway_output", "kitting_output_hu_required"], as_dict=True) or frappe._dict()


def create_kitting_order(kit_item, bom, warehouse, work_center_bin, quantity, direction, putaway_output=None):
    require_role("WMS Operator", "WMS Supervisor")
    bom_doc = frappe.get_doc("BOM", bom)
    if bom_doc.item != kit_item: frappe.throw(_("BOM {0} is not a BOM for item {1}").format(bom, kit_item))
    if bom_doc.docstatus != 1: frappe.throw(_("BOM {0} must be submitted").format(bom))
    if frappe.db.get_value("Storage Bin", work_center_bin, "warehouse") != warehouse:
        frappe.throw(_("Work center bin {0} is not in warehouse {1}").format(work_center_bin, warehouse))
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Quantity must be greater than zero"))
    ratio = quantity / flt(bom_doc.quantity)
    components = [{"item": bi.item_code, "required_qty": flt(bi.stock_qty or bi.qty) * ratio, "stock_uom": bi.stock_uom} for bi in bom_doc.items]
    settings = _settings(warehouse)
    order = frappe.get_doc({
        "doctype": "Kitting Order", "kit_item": kit_item, "bom": bom, "warehouse": warehouse,
        "work_center_bin": work_center_bin, "quantity": quantity, "direction": direction,
        "putaway_output": cint(settings.kitting_putaway_output) if putaway_output is None else cint(putaway_output),
        "status": "Open", "components": components,
    })
    order.insert(ignore_permissions=True)
    if settings.kitting_stage_components == "On Order Creation":
        stage_kitting_components(order.name)
    return order.name


def _kit_uom(order):
    return frappe.db.get_value("WMS Product", {"item": order.kit_item}, "stock_uom") or frappe.db.get_value("Item", order.kit_item, "stock_uom")


def _inputs(order):
    """[(line key, item, quantity, uom)] the order consumes at the work center."""
    if order.direction == "Assemble":
        return [(row.name, row.item, flt(row.required_qty), row.stock_uom) for row in order.components]
    return [("kit", order.kit_item, flt(order.quantity), _kit_uom(order))]


def _outputs(order):
    if order.direction == "Assemble":
        return [(order.kit_item, flt(order.quantity), _kit_uom(order))]
    return [(row.item, flt(row.required_qty), row.stock_uom) for row in order.components]


def _at_work_center(order, item):
    return frappe.get_all("WMS Stock Balance",
        filters={"warehouse": order.warehouse, "product": item, "storage_bin": order.work_center_bin, "stock_type": "AVAILABLE", "available_quantity": [">", 0]},
        fields=["name", "handling_unit", "batch_no", "serial_no", "available_quantity", "shelf_life_expiry_date", "first_receipt_date"])


def _staging_requests(order, line=None):
    filters = {"reference_doctype": "Kitting Order", "reference_name": order.name, "request_type": "Replenish"}
    if line: filters["reference_line"] = line
    return frappe.get_all("Warehouse Request", filters=filters, fields=["name", "reference_line", "requested_quantity", "confirmed_quantity", "status"])


def _in_transit(order, line):
    return sum(max(flt(r.requested_quantity) - flt(r.confirmed_quantity), 0) for r in _staging_requests(order, line) if r.status not in ("Completed", "Cancelled"))


def get_kitting_order(kitting_order_name):
    """The RF Kitting screen: each input with what is required, already at the work center and
    still on its way there."""
    require_role("WMS Operator", "WMS Supervisor")
    order = frappe.get_doc("Kitting Order", kitting_order_name)
    order.check_permission("read")
    settings = _settings(order.warehouse)
    inputs = []
    for line, item, qty, uom in _inputs(order):
        at_wc = sum(flt(b.available_quantity) for b in _at_work_center(order, item))
        inputs.append({"line": line, "item": item, "item_name": frappe.db.get_value("Item", item, "item_name"), "required": qty, "stock_uom": uom,
                       "at_work_center": at_wc, "in_transit": _in_transit(order, line)})
    return {
        "name": order.name, "kit_item": order.kit_item, "kit_item_name": frappe.db.get_value("Item", order.kit_item, "item_name"),
        "direction": order.direction, "quantity": order.quantity, "warehouse": order.warehouse, "work_center_bin": order.work_center_bin,
        "status": order.status, "destination_hu": order.destination_hu, "putaway_output": cint(order.putaway_output),
        "output_hu_required": cint(settings.kitting_output_hu_required),
        "inputs": inputs, "outputs": [{"item": i, "quantity": q, "stock_uom": u} for i, q, u in _outputs(order)],
        "ready": all(flt(i["at_work_center"]) + 0.000001 >= flt(i["required"]) for i in inputs),
    }


def stage_kitting_components(kitting_order_name):
    """Warehouse tasks from storage to the work center for whatever is neither there nor on its
    way yet. Sources follow the product's Removal Rule (FEFO by default), like picking."""
    from frappe_wms.services.allocation import _candidate_balances
    from frappe_wms.services.determination import determine_process_type
    from frappe_wms.services.task import create_tasks_for_request
    require_role("WMS Operator", "WMS Supervisor")
    order = frappe.get_doc("Kitting Order", kitting_order_name, for_update=True)
    if order.status not in ("Draft", "Open"): frappe.throw(_("Kitting Order is not open"))
    created, short = [], []
    for line, item, qty, uom in _inputs(order):
        need = qty - sum(flt(b.available_quantity) for b in _at_work_center(order, item)) - _in_transit(order, line)
        if need <= 0.000001: continue
        row = frappe._dict(item=item, required_stock_type="AVAILABLE", required_batch=None, required_serial_no=None, required_characteristics=None)
        for b in _candidate_balances(row, order.warehouse):
            if need <= 0.000001: break
            if b.storage_bin == order.work_center_bin: continue
            take = min(flt(b.available_quantity), need)
            request = frappe.get_doc({
                "doctype": "Warehouse Request", "request_type": "Replenish", "warehouse": order.warehouse, "product": item,
                "requested_quantity": take, "stock_uom": uom, "source_bin": b.storage_bin, "source_hu": b.handling_unit,
                "batch_no": b.batch_no, "serial_no": b.serial_no, "destination_bin": order.work_center_bin, "stock_type": "AVAILABLE",
                "reference_doctype": "Kitting Order", "reference_name": order.name, "reference_line": line,
                "process_type": determine_process_type(order.warehouse, "Replenish", item=item, stock_type="AVAILABLE", default="REPLENISH"),
                "priority": "High", "status": "Open",
            }).insert(ignore_permissions=True)
            create_tasks_for_request(request.name)
            # Part of an HU is taken out of it (product staging); a whole HU travels as it is.
            if b.handling_unit and take + 0.000001 < flt(frappe.db.sql("select coalesce(sum(quantity),0) from `tabWMS Stock Balance` where handling_unit=%s", b.handling_unit)[0][0]):
                frappe.db.set_value("Warehouse Task", {"warehouse_request": request.name, "docstatus": 0}, "unpack_at_destination", 1, update_modified=False)
            created.append(request.name)
            need -= take
        if need > 0.000001: short.append({"item": item, "missing": need, "stock_uom": uom})
    if short and not created:
        frappe.throw(_("No stock to stage: {0}").format(", ".join(f"{s['item']} ({s['missing']:g} {s['stock_uom']})" for s in short)))
    return {"requests": created, "short": short}


def complete_kitting_order(kitting_order_name, destination_hu=None):
    require_role("WMS Operator", "WMS Supervisor")
    order = frappe.get_doc("Kitting Order", kitting_order_name, for_update=True)
    if order.status not in ("Draft", "Open"): frappe.throw(_("Kitting Order is not open"))
    settings = _settings(order.warehouse)
    destination_hu = (destination_hu or "").strip() or None
    if cint(settings.kitting_output_hu_required) and not destination_hu:
        frappe.throw(_("Scan the Handling Unit the output goes on"))
    if destination_hu:
        from frappe_wms.services.handling_unit import get_or_create_handling_unit
        destination_hu = get_or_create_handling_unit(destination_hu, storage_bin=order.work_center_bin, warehouse=order.warehouse)
        hu_bin = frappe.db.get_value("Handling Unit", destination_hu, "current_bin")
        if hu_bin and hu_bin != order.work_center_bin:
            frappe.throw(_("Handling Unit {0} is in {1}, not at the work center {2}").format(destination_hu, hu_bin, order.work_center_bin))

    # Every input is checked before anything posts, so a later shortage never leaves an earlier
    # input consumed. Consumption takes the rows actually at the work center - each batch, serial
    # and HU its own row - soonest expiry first.
    consumption = []
    for line, item, qty, uom in _inputs(order):
        rows = sorted(_at_work_center(order, item), key=lambda b: (str(b.shelf_life_expiry_date or "9999"), str(b.first_receipt_date or "")))
        have = sum(flt(b.available_quantity) for b in rows)
        if have + 0.000001 < qty:
            frappe.throw(_("Insufficient stock for {0} in {1}: need {2}, have {3}").format(item, order.work_center_bin, qty, have))
        left = qty
        for b in rows:
            if left <= 0.000001: break
            take = min(flt(b.available_quantity), left)
            consumption.append({"item": item, "quantity": take, "stock_uom": uom, "batch_no": b.batch_no, "serial_no": b.serial_no, "handling_unit": b.handling_unit})
            left -= take

    # Each leg posts as its own single-entry call (post_entries' zero-sum check only applies to
    # multi-entry calls) - kitting changes the number of stock units the way a transfer never does.
    movement_type = "801" if order.direction == "Assemble" else "802"
    for i, c in enumerate(consumption, 1):
        post_entries([{"warehouse": order.warehouse, "product": c["item"], "storage_bin": order.work_center_bin, "handling_unit": c["handling_unit"],
            "batch_no": c["batch_no"], "serial_no": c["serial_no"], "stock_type": "AVAILABLE", "quantity": -flt(c["quantity"]),
            "stock_uom": c["stock_uom"], "movement_type": movement_type}], order.doctype, order.name, f"KIT:{order.name}:in:{i}")
    for i, (item, qty, uom) in enumerate(_outputs(order), 1):
        post_entries([{"warehouse": order.warehouse, "product": item, "storage_bin": order.work_center_bin, "handling_unit": destination_hu,
            "stock_type": "AVAILABLE", "quantity": qty, "stock_uom": uom, "movement_type": movement_type}],
            order.doctype, order.name, f"KIT:{order.name}:out:{i}")

    from frappe_wms.services.erp_sync_queue import dispatch
    order.db_set({"status": "Completed", "completed_at": now_datetime(), "completed_by": frappe.session.user, "destination_hu": destination_hu}, update_modified=True)
    _close_staging(order)
    dispatch("kitting_order", order, consumed=consumption)
    putaway = _putaway_output(order, destination_hu) if cint(order.putaway_output) else []
    return {"kitting_order": order.name, "status": "Completed", "destination_hu": destination_hu, "putaway_requests": putaway}


def _putaway_output(order, destination_hu):
    from frappe_wms.services.determination import determine_process_type
    from frappe_wms.services.task import create_tasks_for_request
    created = []
    for item, qty, uom in _outputs(order):
        request = frappe.get_doc({
            "doctype": "Warehouse Request", "request_type": "Putaway", "warehouse": order.warehouse, "product": item,
            "requested_quantity": qty, "stock_uom": uom, "source_bin": order.work_center_bin, "source_hu": destination_hu,
            "stock_type": "AVAILABLE", "reference_doctype": "Kitting Order", "reference_name": order.name,
            "process_type": determine_process_type(order.warehouse, "Putaway", item=item, stock_type="AVAILABLE", default="GR_PUTAWAY"),
            "status": "Open",
        }).insert(ignore_permissions=True)
        # The kit is built either way: a putaway that cannot be planned now (no destination
        # found) stays an open request for the Monitor rather than undoing the completion.
        frappe.db.savepoint("kit_putaway")
        try:
            create_tasks_for_request(request.name)
        except frappe.ValidationError as e:
            frappe.db.rollback(save_point="kit_putaway")
            frappe.clear_messages()
            request.add_comment("Comment", _("Putaway could not be planned yet: {0}").format(e))
        created.append(request.name)
    return created


def _close_staging(order):
    """Staging tasks nobody has started are no longer needed."""
    from frappe_wms.services.warehouse_order import release_next_in_sequence, sync_warehouse_order
    orders = set()
    for r in _staging_requests(order):
        if r.status in ("Completed", "Cancelled"): continue
        for t in frappe.get_all("Warehouse Task", filters={"warehouse_request": r.name, "docstatus": 0}, fields=["name", "warehouse_order"]):
            frappe.db.set_value("Warehouse Task", t.name, {"status": "Cancelled", "docstatus": 2}, update_modified=True)
            if t.warehouse_order: orders.add(t.warehouse_order)
        if not frappe.db.exists("Warehouse Task", {"warehouse_request": r.name, "docstatus": 1}):
            frappe.db.set_value("Warehouse Request", r.name, "status", "Cancelled", update_modified=True)
    for wo in orders:
        release_next_in_sequence(wo)
        sync_warehouse_order(wo)


def cancel_kitting_order(kitting_order_name):
    require_role("WMS Operator", "WMS Supervisor")
    order = frappe.get_doc("Kitting Order", kitting_order_name, for_update=True)
    if order.status not in ("Draft", "Open"): frappe.throw(_("Only an open Kitting Order can be cancelled"))
    _close_staging(order)
    order.db_set("status", "Cancelled", update_modified=True)
    return {"kitting_order": order.name, "status": "Cancelled"}
