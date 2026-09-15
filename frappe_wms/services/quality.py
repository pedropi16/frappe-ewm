import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe_wms.services.stock import transfer_stock
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

def list_open_inspections(user=None):
    resource = my_resource(user)
    filters = {"status": "Draft"}
    if resource: filters["warehouse"] = resource.warehouse
    return frappe.get_list("WMS Quality Inspection", filters=filters,
        fields=["name", "warehouse", "product", "handling_unit", "storage_bin", "from_stock_type",
            "quantity", "stock_uom", "passed_to_stock_type", "failed_to_stock_type", "inspection_date"],
        order_by="inspection_date asc, creation asc", limit=30)

def complete_inspection(inspection_name, passed_quantity=None, failed_quantity=None):
    require_role("WMS Inventory Controller", "WMS Supervisor")
    frappe.db.sql("select name from `tabWMS Quality Inspection` where name=%s for update", inspection_name)
    doc = frappe.get_doc("WMS Quality Inspection", inspection_name)
    if doc.status != "Draft": frappe.throw(_("Inspection has already been completed"))
    passed = flt(passed_quantity) if passed_quantity is not None else flt(doc.passed_quantity)
    failed = flt(failed_quantity) if failed_quantity is not None else flt(doc.failed_quantity)
    if passed < 0 or failed < 0: frappe.throw(_("Passed/failed quantities cannot be negative"))
    if round(passed + failed, 6) != round(flt(doc.quantity), 6):
        frappe.throw(_("Passed and failed quantities must add up to the inspected quantity ({0})").format(doc.quantity))

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
    return {"inspection": doc.name, "status": "Completed", "passed_quantity": passed, "failed_quantity": failed}
