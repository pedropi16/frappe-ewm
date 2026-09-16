import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.determination import determine_destination_bin
from frappe_wms.services.warehouse_order import attach_task
from frappe_wms.utils import require_role


def close_movement(hu_name, device=None):
    # SAP EWM-style "what's next": an HU that just finished one leg of a multi-step journey
    # (e.g. arrived at a deconsolidation area) gets routed to its next bin one hop at a time -
    # no upfront route needed. Calling this again once the HU has arrived determines the *next*
    # hop from wherever it now sits, using whatever Bin Determination Rule matches that bin's
    # storage type, so a chain through an intermediate bin falls out naturally.
    require_role("WMS Operator", "WMS Supervisor")
    hu = frappe.get_doc("Handling Unit", hu_name)
    if not hu.current_bin:
        frappe.throw(_("Handling Unit {0} has no current bin").format(hu_name))
    balances = frappe.get_all(
        "WMS Stock Balance",
        filters={"handling_unit": hu_name, "quantity": [">", 0]},
        fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"],
    )
    if not balances:
        frappe.throw(_("Handling Unit {0} has no stock to advance").format(hu_name))

    source_storage_type = frappe.db.get_value("Storage Bin", hu.current_bin, "storage_type")
    first = balances[0]
    gross_weight_per_unit = frappe.db.get_value("WMS Product", first.product, "gross_weight_per_unit")
    total_qty = sum(flt(b.quantity) for b in balances)
    incoming_weight = flt(gross_weight_per_unit) * total_qty if gross_weight_per_unit else None
    destination_bin = determine_destination_bin({
        "warehouse": hu.warehouse, "activity": "Internal Move", "item": first.product,
        "stock_type": first.stock_type, "hu_type": hu.hu_type, "source_storage_type": source_storage_type,
        "incoming_weight": incoming_weight,
    })
    process_type = frappe.get_cached_doc("Warehouse Process Type", "INTERNAL_MOVE")

    batch_key = frappe.generate_hash(length=10)
    created = []
    for balance in balances:
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": hu.warehouse,
            "product": balance.product, "planned_quantity": balance.quantity, "stock_uom": balance.stock_uom,
            "batch_no": balance.batch_no, "serial_no": balance.serial_no,
            "source_bin": hu.current_bin, "source_hu": hu_name, "destination_bin": destination_bin,
            "stock_type_from": balance.stock_type, "stock_type_to": balance.stock_type,
            "movement_type": process_type.movement_type, "priority": "Normal", "status": "Open",
            # Every line here is the same physical HU making the same hop, not a queue of
            # independent stops - a shared, non-incrementing sequence keeps them from gating
            # against each other under the Warehouse Order sequencing engine.
            "sequence": 0,
        })
        attach_task(task, batch_key)
        task.insert(ignore_permissions=True)
        created.append(task.name)
    return {"destination_bin": destination_bin, "tasks": created}
