import frappe
ROLES=["WMS Operator","WMS Receiver","WMS Picker","WMS Packer","WMS Loader","WMS Inventory Controller","WMS Supervisor","WMS Process Engineer","WMS Master Data","WMS Administrator","WMS Integration User","WMS Auditor"]

def ensure_roles():
    for role in ROLES:
        if not frappe.db.exists("Role",role): frappe.get_doc({"doctype":"Role","role_name":role}).insert(ignore_permissions=True)
