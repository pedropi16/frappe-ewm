"""Ad hoc warehouse tasks for many handling units / stock lines at once (SAP EWM /SCWM/ADHU, /SCWM/ADPROD).

The caller picks the lines with the Monitor's advanced selection; this creates one Internal Move task per
HU content line / stock line, all in one batch, so they form one warehouse order.
"""
import frappe
from frappe import _
from frappe.utils import flt

from frappe_wms.services.bin_rules import incoming_load, validate_destination_bin
from frappe_wms.services.determination import determine_process_type
from frappe_wms.services.stock import dim_values
from frappe_wms.services.warehouse_order import attach_task
from frappe_wms.utils import require_role

BALANCE_FIELDS = ["name", "warehouse", "product", "batch_no", "serial_no", "storage_bin", "handling_unit", "stock_type", "quantity", "available_quantity", "stock_uom"]


def _balances(line):
    """(balance row, quantity) pairs a line stands for: an HU = everything in it, a stock line = its (free) quantity."""
    if line.get("handling_unit") and not line.get("name"):
        rows = frappe.get_all("WMS Stock Balance", filters={"handling_unit": line["handling_unit"], "quantity": [">", 0]}, fields=BALANCE_FIELDS)
        if not rows:
            frappe.throw(_("Handling Unit {0} holds no stock - a task needs something to move").format(line["handling_unit"]))
        return [(r, flt(r.quantity)) for r in rows]
    bal = frappe.get_doc("WMS Stock Balance", line["name"]).as_dict()
    qty = flt(line.get("quantity")) or flt(bal.available_quantity)
    if qty <= 0 or qty > flt(bal.available_quantity) + 1e-6:
        frappe.throw(_("{0} in {1}: only {2} is free to move").format(bal.product, bal.storage_bin, flt(bal.available_quantity)))
    return [(bal, qty)]


def create_adhoc_tasks(lines, destination_bin, priority="Normal"):
    require_role("WMS Operator", "WMS Supervisor")
    if not lines: frappe.throw(_("Select at least one line"))
    if not destination_bin: frappe.throw(_("Enter the destination bin"))
    destination = frappe.get_doc("Storage Bin", destination_bin)
    batch_key = frappe.generate_hash(length=10)
    created = []
    for line in lines:
        for bal, qty in _balances(line):
            if destination.warehouse != bal.warehouse: frappe.throw(_("Destination bin {0} is not in warehouse {1}").format(destination_bin, bal.warehouse))
            if not bal.storage_bin: frappe.throw(_("{0} is not in a bin").format(bal.product))
            hu = bal.handling_unit
            if hu and frappe.db.exists("Warehouse Task", {"source_hu": hu, "docstatus": 0, "status": ["not in", ["Cancelled", "Confirmed"]]}):
                frappe.throw(_("{0} already has an open warehouse task").format(hu))
            # moving all an HU holds moves the HU; part of it lands loose (same rule as create_and_confirm_move)
            whole = hu and frappe.db.sql("select count(*), sum(quantity) from `tabWMS Stock Balance` where handling_unit=%s and quantity>0", hu)[0]
            moves_hu = bool(hu) and whole[0] == 1 and qty >= flt(whole[1]) - 1e-6
            hu_type = frappe.db.get_value("Handling Unit", hu, "hu_type") if hu else None
            iw, iv = incoming_load(bal.product, qty)
            validate_destination_bin(destination_bin, item=bal.product, incoming_quantity=qty, stock_type=bal.stock_type, hu_type=hu_type,
                                     batch_no=bal.batch_no, destination_hu=hu if moves_hu else None, incoming_weight=iw, incoming_volume=iv)
            process_type = frappe.get_cached_doc("Warehouse Process Type", determine_process_type(bal.warehouse, "Internal Move", item=bal.product, stock_type=bal.stock_type, default="INTERNAL_MOVE"))
            task = frappe.get_doc({
                "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": bal.warehouse, "product": bal.product, "planned_quantity": qty,
                "stock_uom": bal.stock_uom, "batch_no": bal.batch_no, "serial_no": bal.serial_no, "source_bin": bal.storage_bin, "source_hu": hu,
                "destination_bin": destination_bin, "unpack_at_destination": 1 if hu and not moves_hu else 0, "stock_type_from": bal.stock_type, "stock_type_to": bal.stock_type,
                **dim_values(bal), "movement_type": process_type.movement_type, "priority": priority or "Normal", "status": "Open"})
            attach_task(task, batch_key, default_queue=process_type.default_queue)
            task.insert(ignore_permissions=True)
            created.append(task.name)
    return created
