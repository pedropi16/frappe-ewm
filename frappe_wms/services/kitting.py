import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import post_entries
from frappe_wms.services.erpnext_sync import sync_kitting_order
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

def create_kitting_order(kit_item, bom, warehouse, work_center_bin, quantity, direction):
    require_role("WMS Operator", "WMS Supervisor")
    bom_doc = frappe.get_doc("BOM", bom)
    if bom_doc.item != kit_item: frappe.throw(_("BOM {0} is not a BOM for item {1}").format(bom, kit_item))
    if bom_doc.docstatus != 1: frappe.throw(_("BOM {0} must be submitted").format(bom))
    quantity = flt(quantity)
    ratio = quantity / flt(bom_doc.quantity)
    components = [{"item": bi.item_code, "required_qty": flt(bi.qty) * ratio, "stock_uom": bi.stock_uom} for bi in bom_doc.items]
    order = frappe.get_doc({
        "doctype": "Kitting Order", "kit_item": kit_item, "bom": bom, "warehouse": warehouse,
        "work_center_bin": work_center_bin, "quantity": quantity, "direction": direction,
        "status": "Open", "components": components,
    })
    order.insert(ignore_permissions=True)
    return order.name

def _source_balance(warehouse, product, storage_bin):
    # The specific balance row (HU included) actually holding this stock - WMS Stock Balance
    # is keyed by handling_unit among other dimensions, so a post_entries call with no
    # handling_unit targets a *different*, normally-empty "loose" balance rather than
    # whatever HU the stock actually arrived on (e.g. via a Goods Receipt, which always
    # requires one). Consuming must target the real row, not assume a loose one.
    return frappe.db.get_value("WMS Stock Balance",
        {"warehouse": warehouse, "product": product, "storage_bin": storage_bin, "stock_type": "AVAILABLE", "available_quantity": [">", 0]},
        ["handling_unit", "available_quantity"], as_dict=True)

def complete_kitting_order(kitting_order_name):
    require_role("WMS Operator", "WMS Supervisor")
    frappe.db.sql("select name from `tabKitting Order` where name=%s for update", kitting_order_name)
    order = frappe.get_doc("Kitting Order", kitting_order_name)
    if order.status not in ("Draft", "Open"): frappe.throw(_("Kitting Order is not open"))
    kit_uom = frappe.db.get_value("WMS Product", {"item": order.kit_item}, "stock_uom") or frappe.db.get_value("Item", order.kit_item, "stock_uom")
    # Each leg posts as its own single-entry call (post_entries' zero-sum check only applies
    # when len(entries) > 1) - same precedent as post_goods_receipt, since kitting changes the
    # total quantity of stock units the way a pure transfer never does. Validated explicitly
    # up front (not left to _upsert_balance's own insufficient-stock guard to catch partway
    # through) so a later component's shortage can never leave an earlier one already consumed.
    # The output (kit item on Assemble, components on Disassemble) posts loose (no
    # handling_unit) - registering it onto a specific HU is a separate, later operator action,
    # out of scope for this pass (Kitting Order has no destination-HU field).
    consumed = {}
    if order.direction == "Assemble":
        for row in order.components:
            balance = _source_balance(order.warehouse, row.item, order.work_center_bin)
            if not balance or flt(balance.available_quantity) < flt(row.required_qty):
                frappe.throw(_("Insufficient stock for component {0} in {1}: need {2}, have {3}").format(row.item, order.work_center_bin, row.required_qty, flt(balance.available_quantity) if balance else 0))
            consumed[row.item] = balance.handling_unit
    else:
        balance = _source_balance(order.warehouse, order.kit_item, order.work_center_bin)
        if not balance or flt(balance.available_quantity) < flt(order.quantity):
            frappe.throw(_("Insufficient stock for {0} in {1}: need {2}, have {3}").format(order.kit_item, order.work_center_bin, order.quantity, flt(balance.available_quantity) if balance else 0))
        consumed[order.kit_item] = balance.handling_unit

    movement_type = "801" if order.direction == "Assemble" else "802"
    if order.direction == "Assemble":
        for i, row in enumerate(order.components, 1):
            post_entries([{"warehouse": order.warehouse, "product": row.item, "storage_bin": order.work_center_bin, "handling_unit": consumed[row.item], "stock_type": "AVAILABLE",
                "quantity": -flt(row.required_qty), "stock_uom": row.stock_uom, "movement_type": movement_type}],
                order.doctype, order.name, f"KIT:{order.name}:comp:{i}")
        post_entries([{"warehouse": order.warehouse, "product": order.kit_item, "storage_bin": order.work_center_bin, "stock_type": "AVAILABLE",
            "quantity": flt(order.quantity), "stock_uom": kit_uom, "movement_type": movement_type}],
            order.doctype, order.name, f"KIT:{order.name}:kit")
    else:
        post_entries([{"warehouse": order.warehouse, "product": order.kit_item, "storage_bin": order.work_center_bin, "handling_unit": consumed[order.kit_item], "stock_type": "AVAILABLE",
            "quantity": -flt(order.quantity), "stock_uom": kit_uom, "movement_type": movement_type}],
            order.doctype, order.name, f"KIT:{order.name}:kit")
        for i, row in enumerate(order.components, 1):
            post_entries([{"warehouse": order.warehouse, "product": row.item, "storage_bin": order.work_center_bin, "stock_type": "AVAILABLE",
                "quantity": flt(row.required_qty), "stock_uom": row.stock_uom, "movement_type": movement_type}],
                order.doctype, order.name, f"KIT:{order.name}:comp:{i}")

    erpnext_entry = sync_kitting_order(order)
    updates = {"status": "Completed", "completed_at": now_datetime(), "completed_by": frappe.session.user}
    if erpnext_entry: updates["erpnext_stock_entry"] = erpnext_entry
    order.db_set(updates, update_modified=True)
    return {"kitting_order": order.name, "status": "Completed"}
