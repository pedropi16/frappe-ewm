import frappe


def execute():
    # HU Requirement (Forbidden/Optional/Mandatory) supersedes the yes/no "HU managed" flag; Optional is the column default.
    if frappe.db.has_column("Storage Type", "hu_requirement"):
        frappe.db.sql("update `tabStorage Type` set hu_requirement='Mandatory' where hu_managed=1")
