import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.determination import determine_process_type
from frappe_wms.services.warehouse_order import attach_task
from frappe_wms.utils import require_role


def create_deconsolidation_tasks(source_hu, lines):
    # Breaking a mixed receiving HU's contents out across several destination bins/HUs -
    # SAP EWM's Deconsolidation. Modeled as plain Warehouse Tasks (one per destination line)
    # sharing a batch_key, so they inherit the same Warehouse Order sequencing/gating and the
    # same RF confirmation wizard every other task type already uses, instead of a bespoke
    # execution path.
    require_role("WMS Operator", "WMS Supervisor")
    if not lines:
        frappe.throw(_("Add at least one destination line to deconsolidate"))
    hu = frappe.get_doc("Handling Unit", source_hu)
    if not hu.current_bin:
        frappe.throw(_("Handling Unit {0} has no current bin").format(source_hu))
    process_type_name = determine_process_type(hu.warehouse, "Deconsolidation", item=lines[0].get("product"), stock_type=lines[0].get("stock_type"), default="DECON")
    process_type = frappe.get_cached_doc("Warehouse Process Type", process_type_name)

    available = {}
    for row in frappe.get_all(
        "WMS Stock Balance",
        filters={"handling_unit": source_hu, "quantity": [">", 0]},
        fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"],
    ):
        key = (row.product, row.batch_no or "", row.serial_no or "", row.stock_type)
        available[key] = available.get(key, 0) + flt(row.quantity)

    requested = {}
    for line in lines:
        if not line.get("destination_bin") and not line.get("destination_hu"):
            frappe.throw(_("Each deconsolidation line needs a destination bin or Handling Unit"))
        key = (line["product"], line.get("batch_no") or "", line.get("serial_no") or "", line["stock_type"])
        requested[key] = requested.get(key, 0) + flt(line["quantity"])
    for key, qty in requested.items():
        if round(qty, 6) > round(available.get(key, 0), 6):
            frappe.throw(_("Requested quantity for {0} exceeds what is on Handling Unit {1}").format(key[0], source_hu))

    stock_uom_by_key = {
        (r.product, r.batch_no or "", r.serial_no or "", r.stock_type): r.stock_uom
        for r in frappe.get_all("WMS Stock Balance", filters={"handling_unit": source_hu}, fields=["product", "batch_no", "serial_no", "stock_type", "stock_uom"])
    }

    batch_key = frappe.generate_hash(length=10)
    created = []
    for line in lines:
        key = (line["product"], line.get("batch_no") or "", line.get("serial_no") or "", line["stock_type"])
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Deconsolidation", "warehouse": hu.warehouse,
            "product": line["product"], "planned_quantity": line["quantity"],
            "stock_uom": stock_uom_by_key.get(key), "batch_no": line.get("batch_no"), "serial_no": line.get("serial_no"),
            "source_bin": hu.current_bin, "source_hu": source_hu,
            "destination_bin": line.get("destination_bin"), "destination_hu": line.get("destination_hu"),
            "stock_type_from": line["stock_type"], "stock_type_to": line["stock_type"],
            "movement_type": process_type.movement_type, "priority": "Normal", "status": "Open",
            "consolidation_group_line": line.get("consolidation_group_line"),
        })
        attach_task(task, batch_key)
        task.insert(ignore_permissions=True)
        created.append(task.name)
    return created
