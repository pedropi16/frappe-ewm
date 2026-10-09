import frappe
from frappe.utils import flt

from frappe_wms.services.task import task_names_for_allocations
from frappe_wms.utils import require_wms_access

# What the delivery maintenance screens (the Outbound / Inbound Delivery forms) show on their tabs besides the delivery's own fields: tasks, handling units,
# transport, receipts / issues, references and dates - one read for the whole screen.

TASK_FIELDS = ["name", "task_type", "status", "product", "planned_quantity", "confirmed_quantity", "stock_uom", "batch_no", "serial_no", "source_bin", "destination_bin",
               "source_hu", "destination_hu", "assigned_resource", "warehouse_order", "confirmed_at"]


def _hus(names):
    names = [n for n in dict.fromkeys(names) if n]
    out = []
    for hu in frappe.get_all("Handling Unit", filters={"name": ["in", names or [""]]}, fields=["name", "hu_type", "current_bin", "status", "parent_hu"]):
        hu["lines"] = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu.name, "quantity": [">", 0]}, fields=["product", "quantity", "batch_no", "serial_no", "storage_bin"])
        out.append(hu)
    return out


def _transport(shipments=(), inbound_delivery=None):
    filters = [["shipment", "in", list(shipments) or [""]]]
    units = frappe.get_all("WMS Transportation Unit", or_filters=[*filters, *([["inbound_delivery", "=", inbound_delivery]] if inbound_delivery else [])],
                           fields=["name", "unit_type", "status", "activity_status", "vehicle_registration", "carrier", "door", "yard_bin", "seal_number", "dock_appointment", "arrived_at", "departed_at"])
    appointments = frappe.get_all("WMS Dock Appointment", or_filters=[["shipment", "in", list(shipments) or [""]], *([["inbound_delivery", "=", inbound_delivery]] if inbound_delivery else [])],
                                  fields=["name", "direction", "status", "planned_start", "door", "carrier", "vehicle_registration", "checked_in_at", "completed_at"])
    return units, appointments


def delivery_view(doctype, name):
    require_wms_access()
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    if doctype not in ("Outbound Delivery", "Inbound Delivery"): frappe.throw("Unsupported document")
    from frappe_wms.services.locks import status
    return {**(_outbound(doc) if doctype == "Outbound Delivery" else _inbound(doc)), "lock": status(doctype, name)}


def _outbound(doc):
    allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": doc.name}, fields=["name", "product", "storage_bin", "handling_unit", "batch_no", "serial_no", "allocated_quantity", "picked_quantity", "status"])
    task_names = list(task_names_for_allocations([a.name for a in allocations]))
    tasks = frappe.get_all("Warehouse Task", filters={"name": ["in", task_names or [""]]}, fields=TASK_FIELDS, order_by="sequence asc, creation asc")
    requests = frappe.get_all("Warehouse Request", filters={"request_type": "Cross Dock", "reference_doctype": "Outbound Delivery", "reference_name": doc.name}, fields=["name", "status", "product", "requested_quantity", "source_bin", "destination_bin"])
    packing = frappe.get_all("Packing Order", filters={"outbound_delivery": doc.name}, fields=["name", "status", "work_center_bin"])
    issues = frappe.get_all("Goods Issue", filters={"outbound_delivery": doc.name}, fields=["name", "status", "posting_datetime", "reversed", "shipment"])
    issue_hus = frappe.get_all("Goods Issue Item", filters={"parent": ["in", [i.name for i in issues] or [""]]}, pluck="handling_unit")
    hu_names = [a.handling_unit for a in allocations] + [t.destination_hu for t in tasks] + issue_hus
    shipments = frappe.get_all("Shipment Delivery", filters={"outbound_delivery": doc.name, "parenttype": "WMS Shipment"}, pluck="parent")
    shipment_rows = frappe.get_all("WMS Shipment", filters={"name": ["in", shipments or [""]]}, fields=["name", "status", "route", "carrier", "vehicle_registration", "planned_departure", "actual_departure", "door", "seal_number"])
    units, appointments = _transport(shipments)
    return {"allocations": allocations, "tasks": tasks, "requests": requests, "packing_orders": packing, "goods_issues": issues, "handling_units": _hus(hu_names),
            "shipments": shipment_rows, "transport_units": units, "appointments": appointments,
            "references": {"sales_orders": sorted({i.sales_order for i in doc.items if i.sales_order}), "erp_source": [doc.erp_source_doctype, doc.erp_source_name],
                           "delivery_request": doc.delivery_request, "external_reference": doc.external_reference, "customer_instruction": doc.customer_instruction_reference},
            "dates": {"created": doc.creation, "changed": doc.modified, "delivery_date": doc.delivery_date, "first_task": min([t.confirmed_at for t in tasks if t.confirmed_at] or [None]),
                      "goods_issue": max([i.posting_datetime for i in issues if i.posting_datetime] or [None])}}


def _inbound(doc):
    from frappe_wms.services.receipt import inbound_overview
    base = inbound_overview(doc.name)
    units, appointments = _transport(inbound_delivery=doc.name)
    return {**base, "transport_units": units, "appointments": appointments, "handling_units": base["handling_units"],
            "references": {"purchase_orders": sorted({i.purchase_order for i in doc.items if i.purchase_order}), "erp_source": [doc.erp_source_doctype, doc.erp_source_name],
                           "delivery_request": doc.delivery_request, "external_reference": doc.external_reference},
            "dates": {"created": doc.creation, "changed": doc.modified, "expected_arrival": doc.expected_arrival, "posting_date": doc.posting_date,
                      "first_receipt": min([r.creation for r in base["receipts"]] or [None])}}
