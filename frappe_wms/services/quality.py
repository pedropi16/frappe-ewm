import frappe
from frappe import _
from frappe.utils import flt, now_datetime, today
from frappe_wms.services.stock import transfer_stock
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

def _erpnext_reference_for_goods_receipt(goods_receipt):
    gr = frappe.db.get_value("Goods Receipt", goods_receipt, ["erpnext_purchase_receipt", "erpnext_stock_entry"], as_dict=True)
    if not gr: return None, None
    if gr.erpnext_purchase_receipt: return "Purchase Receipt", gr.erpnext_purchase_receipt
    if gr.erpnext_stock_entry: return "Stock Entry", gr.erpnext_stock_entry
    return None, None

def _create_erpnext_quality_inspection(doc, failed):
    # Best-effort: an inspection with no goods_receipt link (created directly, not via an
    # Inspection Rule) or whose Goods Receipt hasn't mirrored to ERPNext yet has nothing to
    # link against - the WMS-side record is complete either way, so just skip the mirror.
    if not doc.goods_receipt: return None
    reference_type, reference_name = _erpnext_reference_for_goods_receipt(doc.goods_receipt)
    if not reference_type: return None
    qi = frappe.get_doc({
        "doctype": "Quality Inspection", "report_date": today(), "inspection_type": "Incoming",
        "reference_type": reference_type, "reference_name": reference_name, "item_code": doc.product,
        "batch_no": doc.batch_no, "sample_size": doc.quantity, "inspected_by": frappe.session.user,
        # ERPNext's status is binary (Accepted/Rejected) - a partial pass with some failed
        # units is recorded as Rejected here; the exact passed/failed split still lives on the
        # WMS Quality Inspection record, so nothing is actually lost.
        "status": "Rejected" if failed > 0 else "Accepted",
    })
    qi.insert(ignore_permissions=True)
    qi.submit()
    return qi.name

def list_open_inspections(user=None):
    require_role("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
    resource = my_resource(user)
    filters = {"status": "Draft"}
    if resource: filters["warehouse"] = resource.warehouse
    return frappe.get_list("WMS Quality Inspection", filters=filters,
        fields=["name", "warehouse", "product", "handling_unit", "storage_bin", "from_stock_type",
            "quantity", "stock_uom", "passed_to_stock_type", "failed_to_stock_type", "inspection_date"],
        order_by="inspection_date asc, creation asc", limit=30)

def generate_samples(doc):
    """Sample plan of an inspection with a sampling percentage below 100: of the HUs nested in the inspected HU the given share (evenly spread,
    at least one), else a quantity of units of the inspected stock."""
    import math
    pct = flt(doc.sampling_percentage)
    if not pct or pct >= 100: return
    children = frappe.get_all("Handling Unit", filters={"parent_hu": doc.handling_unit}, pluck="name", order_by="name asc") if doc.handling_unit else []
    rows = []
    if children:
        count = max(1, math.ceil(len(children) * pct / 100))
        step = len(children) / count
        rows = [{"handling_unit": children[int(i * step)], "quantity_sampled": 0, "result": "Pending"} for i in range(count)]
    else:
        rows = [{"handling_unit": doc.handling_unit, "quantity_sampled": max(1, math.ceil(flt(doc.quantity) * pct / 100)), "result": "Pending"}]
    doc.set("samples", rows)
    doc.save(ignore_permissions=True)

def evaluate_samples(doc):
    """Accept when no more samples failed than acceptable, Reject when more did, Pending while results are missing and still undecided."""
    failed = sum(1 for s in doc.samples if s.result == "Fail")
    if failed > (doc.acceptable_failures or 0): return "Reject"
    return "Pending" if any(s.result == "Pending" for s in doc.samples) else "Accept"

def record_sample_result(inspection_name, sample_name, result, remarks=None):
    require_role("WMS Operator", "WMS Inventory Controller", "WMS Supervisor")
    if result not in ("Pass", "Fail"): frappe.throw(_("Result must be Pass or Fail"))
    doc = frappe.get_doc("WMS Quality Inspection", inspection_name, for_update=True)
    if doc.status != "Draft": frappe.throw(_("Inspection has already been completed"))
    row = next((s for s in doc.samples if s.name == sample_name), None)
    if not row: frappe.throw(_("Sample not found"))
    row.result, row.remarks = result, remarks
    doc.sample_result = evaluate_samples(doc)
    doc.save(ignore_permissions=True)
    return doc.sample_result

def _apply_decisions(doc, decisions):
    """Move each decided quantity to its usage decision's stock type and raise its follow-up task."""
    from frappe_wms.services.task import create_tasks_for_request
    from frappe_wms.services.determination import determine_process_type
    total = round(sum(flt(d["quantity"]) for d in decisions), 6)
    if total != round(flt(doc.quantity), 6): frappe.throw(_("The decisions must add up to the inspected quantity ({0})").format(doc.quantity))
    doc.set("decisions", [{"usage_decision": d["usage_decision"], "quantity": flt(d["quantity"])} for d in decisions])
    passed = failed = 0
    base = {"warehouse": doc.warehouse, "batch_no": doc.batch_no, "serial_no": doc.serial_no, "handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_uom": doc.stock_uom}
    for n, d in enumerate(doc.decisions, 1):
        ud = frappe.get_cached_doc("WMS Usage Decision", d.usage_decision)
        if not ud.active: frappe.throw(_("Usage decision {0} is not active").format(ud.name))
        if d.quantity <= 0: frappe.throw(_("Decision quantities must be above zero"))
        passed += d.quantity if ud.category == "Accept" else 0
        failed += d.quantity if ud.category == "Reject" else 0
        if ud.target_stock_type != doc.from_stock_type:
            transfer_stock(source={**base, "product": doc.product, "stock_type": doc.from_stock_type}, destination={"handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_type": ud.target_stock_type},
                quantity=d.quantity, movement_type="501", reference_doctype=doc.doctype, reference_name=doc.name, idempotency_key=f"QI:{doc.name}:ud:{n}")
        if ud.follow_up_action == "Move to Bin" and ud.follow_up_bin and ud.follow_up_bin != doc.storage_bin:
            request = frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Internal Move", "warehouse": doc.warehouse, "product": doc.product, "requested_quantity": d.quantity, "stock_uom": doc.stock_uom,
                "source_bin": doc.storage_bin, "source_hu": doc.handling_unit, "destination_bin": ud.follow_up_bin, "stock_type": ud.target_stock_type, "batch_no": doc.batch_no, "serial_no": doc.serial_no,
                "reference_doctype": doc.doctype, "reference_name": doc.name, "process_type": determine_process_type(doc.warehouse, "Internal Move", item=doc.product, stock_type=ud.target_stock_type, default="INTERNAL_MOVE"),
                "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
            create_tasks_for_request(request.name)
    return passed, failed

def complete_inspection(inspection_name, passed_quantity=None, failed_quantity=None, decisions=None):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    doc = frappe.get_doc("WMS Quality Inspection", inspection_name, for_update=True)
    if doc.status != "Draft": frappe.throw(_("Inspection has already been completed"))
    if decisions:
        passed, failed = _apply_decisions(doc, decisions)
        doc.db_set({"passed_quantity": passed, "failed_quantity": failed, "status": "Completed", "inspector": frappe.session.user, "completed_at": now_datetime()}, update_modified=True)
        for row in doc.decisions: row.db_update()
        from frappe_wms.services.erp_sync_queue import dispatch
        dispatch("quality_inspection", doc)
        return {"inspection": doc.name, "status": "Completed", "passed_quantity": passed, "failed_quantity": failed}
    if passed_quantity is None and failed_quantity is None and doc.samples and not (flt(doc.passed_quantity) or flt(doc.failed_quantity)):
        verdict = evaluate_samples(doc)  # sampled inspection: the samples decide for the whole quantity
        if verdict == "Pending": frappe.throw(_("Record the result of every sample first"))
        passed_quantity, failed_quantity = (doc.quantity, 0) if verdict == "Accept" else (0, doc.quantity)
    passed = flt(passed_quantity) if passed_quantity is not None else flt(doc.passed_quantity)
    failed = flt(failed_quantity) if failed_quantity is not None else flt(doc.failed_quantity)
    if passed < 0 or failed < 0: frappe.throw(_("Passed/failed quantities cannot be negative"))
    if round(passed + failed, 6) != round(flt(doc.quantity), 6):
        frappe.throw(_("Passed and failed quantities must add up to the inspected quantity ({0})").format(doc.quantity))

    # The inspection records where the stock was when it was created (the receiving bin, for one
    # raised at Goods Receipt), but QUALITY stock doesn't wait there - putaway moves the HU on to a
    # quality/storage bin. Posting against the stale bin failed with "Insufficient stock" on every
    # attempt (reproduced in a simulated shift: every inspection completed after putaway). The HU
    # travels with the stock, so its current bin is where the inspected quantity actually is.
    if doc.handling_unit:
        current_bin = frappe.db.get_value("Handling Unit", doc.handling_unit, "current_bin")
        if current_bin and current_bin != doc.storage_bin:
            doc.db_set("storage_bin", current_bin, update_modified=False)
    base = {"warehouse": doc.warehouse, "batch_no": doc.batch_no, "serial_no": doc.serial_no, "handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_uom": doc.stock_uom}
    if passed > 0:
        source = {**base, "product": doc.product, "stock_type": doc.from_stock_type}
        destination = {"handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_type": doc.passed_to_stock_type}
        transfer_stock(source=source, destination=destination, quantity=passed, movement_type="501", reference_doctype=doc.doctype, reference_name=doc.name, idempotency_key=f"QI:{doc.name}:pass")
    if failed > 0:
        source = {**base, "product": doc.product, "stock_type": doc.from_stock_type}
        destination = {"handling_unit": doc.handling_unit, "storage_bin": doc.storage_bin, "stock_type": doc.failed_to_stock_type}
        transfer_stock(source=source, destination=destination, quantity=failed, movement_type="501", reference_doctype=doc.doctype, reference_name=doc.name, idempotency_key=f"QI:{doc.name}:fail")

    doc.db_set({"passed_quantity": passed, "failed_quantity": failed, "status": "Completed", "inspector": frappe.session.user, "completed_at": now_datetime()}, update_modified=True)
    from frappe_wms.services.erp_sync_queue import dispatch
    dispatch("quality_inspection", doc)
    return {"inspection": doc.name, "status": "Completed", "passed_quantity": passed, "failed_quantity": failed}
