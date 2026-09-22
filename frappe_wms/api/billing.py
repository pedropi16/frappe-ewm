import frappe
from frappe_wms.services.billing import generate_billing_for_period as _generate_billing_for_period, create_billing_sales_invoice as _create_billing_sales_invoice

@frappe.whitelist()
def generate_billing_for_period(warehouse, customer, from_date, to_date):
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    return _generate_billing_for_period(warehouse, customer, from_date, to_date)

@frappe.whitelist()
def create_billing_sales_invoice(warehouse, customer, from_date, to_date):
    return _create_billing_sales_invoice(warehouse, customer, from_date, to_date)
