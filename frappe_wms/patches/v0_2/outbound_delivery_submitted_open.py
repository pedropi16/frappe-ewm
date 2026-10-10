import frappe


def execute():
    frappe.db.sql("update `tabOutbound Delivery` set status='Open' where docstatus=1 and status='Draft'")
