import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.utils import require_role

def _outbound_delivery_for_task(task):
    # A task's customer is only ever traceable through the Outbound Delivery it actually
    # serves - a Pick task via its Stock Allocation, a Cross Dock task via its Warehouse
    # Request's reference. Any other task (inbound-side, internal, unlinked) has no
    # customer to bill and is excluded rather than guessed at.
    if task.stock_allocation:
        return frappe.db.get_value("Stock Allocation", task.stock_allocation, "outbound_delivery")
    if task.warehouse_request:
        request = frappe.db.get_value("Warehouse Request", task.warehouse_request, ["reference_doctype", "reference_name"], as_dict=True)
        if request and request.reference_doctype == "Outbound Delivery":
            return request.reference_name
    return None

def _matching_billing_rate(rates, warehouse, customer, activity):
    for r in rates:
        if r.warehouse and r.warehouse != warehouse: continue
        if r.customer and r.customer != customer: continue
        if r.activity != activity: continue
        return r
    return None

def generate_billing_for_period(warehouse, customer, from_date, to_date):
    tasks = frappe.get_all("Warehouse Task", filters={
        "warehouse": warehouse, "status": "Confirmed",
        "confirmed_at": ["between", [from_date, f"{to_date} 23:59:59"]],
    }, fields=["name", "task_type", "confirmed_quantity", "stock_allocation", "warehouse_request"])
    if not tasks: return []

    by_type = {}
    for t in tasks:
        delivery = _outbound_delivery_for_task(t)
        if not delivery: continue
        if frappe.db.get_value("Outbound Delivery", delivery, "customer") != customer: continue
        bucket = by_type.setdefault(t.task_type, {"task_count": 0, "quantity": 0.0})
        bucket["task_count"] += 1
        bucket["quantity"] += flt(t.confirmed_quantity)
    if not by_type: return []

    rates = frappe.get_all("Billing Rate", filters={"active": 1},
        fields=["warehouse", "customer", "activity", "uom_basis", "rate", "billing_item"], order_by="priority asc")
    lines = []
    for activity, bucket in by_type.items():
        rate = _matching_billing_rate(rates, warehouse, customer, activity)
        if not rate: continue
        basis_qty = bucket["task_count"] if rate.uom_basis == "Per Task" else bucket["quantity"]
        lines.append({
            "activity": activity, "task_count": bucket["task_count"], "quantity": bucket["quantity"],
            "uom_basis": rate.uom_basis, "rate": flt(rate.rate), "billing_item": rate.billing_item,
            "billed_quantity": basis_qty, "charge": flt(rate.rate) * flt(basis_qty),
        })
    return lines

def create_billing_sales_invoice(warehouse, customer, from_date, to_date):
    require_role("WMS Supervisor")
    lines = generate_billing_for_period(warehouse, customer, from_date, to_date)
    if not lines: frappe.throw(_("No billable activity found for {0} in this period").format(customer))
    company = frappe.db.get_value("WMS Warehouse", warehouse, "company")
    invoice = frappe.get_doc({
        "doctype": "Sales Invoice", "customer": customer, "company": company,
        "items": [{"item_code": line["billing_item"], "qty": line["billed_quantity"], "rate": line["rate"]} for line in lines],
    })
    invoice.insert(ignore_permissions=True)
    return invoice.name
