import frappe


def execute():
    # Warehouse Request had no batch_no/serial_no, so every Putaway/Cross Dock task raised from a
    # Goods Receipt of batch- or serial-controlled stock (and every Replenish task pulling one)
    # was created without them - confirming it then looked for a batch-less balance row that
    # never exists and failed with "Insufficient stock ... in bin <receiving bin>" on every
    # attempt, stranding that stock in receiving for good. Fill both in from what the source is
    # actually holding, but only where that's unambiguous (exactly one batch/serial combination
    # for this product on the source HU/bin); anything ambiguous is left for a supervisor.
    tasks = frappe.get_all(
        "Warehouse Task",
        filters={"docstatus": 0, "status": ["not in", ["Confirmed", "Cancelled"]], "batch_no": ["in", ("", None)],
                 "serial_no": ["in", ("", None)], "product": ["is", "set"]},
        fields=["name", "warehouse", "product", "source_bin", "source_hu", "stock_type_from", "warehouse_request"],
    )
    for task in tasks:
        if not frappe.db.get_value("WMS Product", task.product, "batch_control") and \
                frappe.db.get_value("WMS Product", task.product, "serial_control") in (None, "", "None"):
            continue
        filters = {"warehouse": task.warehouse, "product": task.product, "storage_bin": task.source_bin,
                   "stock_type": task.stock_type_from, "quantity": [">", 0]}
        filters["handling_unit"] = task.source_hu or ["in", ("", None)]
        combos = {(b.batch_no or None, b.serial_no or None) for b in frappe.get_all("WMS Stock Balance", filters=filters, fields=["batch_no", "serial_no"])}
        if len(combos) != 1:
            continue
        batch_no, serial_no = combos.pop()
        if not batch_no and not serial_no:
            continue
        frappe.db.set_value("Warehouse Task", task.name, {"batch_no": batch_no, "serial_no": serial_no}, update_modified=False)
        if task.warehouse_request:
            frappe.db.set_value("Warehouse Request", task.warehouse_request, {"batch_no": batch_no, "serial_no": serial_no}, update_modified=False)
