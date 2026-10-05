import frappe


def execute():
    # HU Requirement (Forbidden/Optional/Mandatory) supersedes the yes/no "HU managed" flag. The new column's default fills every
    # existing row, so set each one from the old flag.
    if frappe.db.has_column("Storage Type", "hu_requirement"):
        frappe.db.sql("update `tabStorage Type` set hu_requirement = if(hu_managed = 1, 'Mandatory', 'Optional')")
