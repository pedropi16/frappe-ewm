import frappe
from frappe.utils import flt
from frappe_wms.services.determination import determine_destination_bin, determine_process_type, _preferred_storage_type
from frappe_wms.services.warehouse_order import attach_task
from frappe_wms.utils import require_role

def analyze_slotting(warehouse, from_date=None, to_date=None, min_picks=5):
    # A recommendation is "this item isn't in its own configured preferred_storage_type"
    # (P1's WMS Product / WMS Product Warehouse), evidenced by real pick activity from the
    # ledger - not a from-scratch velocity/optimization model. Only products with genuine
    # recent picks (min_picks) are flagged, so a merely-misplaced-but-idle item isn't churned.
    recommendations = []
    for item in frappe.get_all("WMS Product", filters={"warehouse_managed": 1, "active": 1}, pluck="item"):
        preferred = _preferred_storage_type(item, warehouse)
        if not preferred: continue
        pick_count = frappe.db.sql("""
            select count(*) from `tabWMS Stock Ledger Entry`
            where warehouse=%(warehouse)s and product=%(product)s and movement_type='401'
                and (%(from_date)s is null or date(posting_datetime) >= %(from_date)s)
                and (%(to_date)s is null or date(posting_datetime) <= %(to_date)s)
        """, {"warehouse": warehouse, "product": item, "from_date": from_date, "to_date": to_date})[0][0]
        if pick_count < min_picks: continue
        balances = frappe.get_all("WMS Stock Balance", filters={"warehouse": warehouse, "product": item, "quantity": [">", 0]},
            fields=["storage_bin", "stock_type", "handling_unit", "quantity"])
        for b in balances:
            current_storage_type = frappe.db.get_value("Storage Bin", b.storage_bin, "storage_type")
            if current_storage_type == preferred: continue
            recommendations.append({
                "product": item, "current_bin": b.storage_bin, "current_storage_type": current_storage_type,
                "preferred_storage_type": preferred, "stock_type": b.stock_type, "handling_unit": b.handling_unit,
                "quantity": b.quantity, "recent_picks": pick_count,
            })
    return recommendations

def generate_rearrangement_tasks(warehouse, recommendations=None):
    # Ordinary, operator-confirmable Internal Move tasks (Open, not auto-confirmed) built via
    # the same determine_destination_bin call shape movement.py::close_movement already uses -
    # a rule-less warehouse (no "Internal Move" Bin Determination Rule configured) is skipped
    # row-by-row rather than failing the whole batch, so one bad row doesn't block the rest.
    require_role("WMS Operator", "WMS Supervisor")
    recommendations = analyze_slotting(warehouse) if recommendations is None else recommendations
    created = []
    for rec in recommendations:
        hu_type = frappe.db.get_value("Handling Unit", rec.get("handling_unit"), "hu_type") if rec.get("handling_unit") else None
        try:
            destination_bin = determine_destination_bin({
                "warehouse": warehouse, "activity": "Internal Move", "item": rec["product"],
                "stock_type": rec["stock_type"], "hu_type": hu_type, "source_storage_type": rec["current_storage_type"],
                "destination_hu": rec.get("handling_unit"),
            })
        except frappe.ValidationError:
            continue
        process_type_name = determine_process_type(warehouse, "Internal Move", item=rec["product"], stock_type=rec["stock_type"], default="INTERNAL_MOVE")
        process_type = frappe.get_cached_doc("Warehouse Process Type", process_type_name)
        stock_uom = frappe.db.get_value("WMS Product", {"item": rec["product"]}, "stock_uom")
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": warehouse,
            "product": rec["product"], "planned_quantity": flt(rec["quantity"]), "stock_uom": stock_uom,
            "source_bin": rec["current_bin"], "source_hu": rec.get("handling_unit"), "destination_bin": destination_bin,
            "stock_type_from": rec["stock_type"], "stock_type_to": rec["stock_type"],
            "movement_type": process_type.movement_type, "priority": "Low", "status": "Open",
        })
        attach_task(task, frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        created.append(task.name)
    return created
