import frappe

from frappe_wms.utils import generate_check_digits


def execute():
    # check_digits only auto-generates on insert (Storage Bin.before_insert) - every bin created
    # before this feature shipped has it blank. Required before "Require Bin Check Digits" can
    # safely be turned on anywhere, or confirming a task against a blank check digit would be
    # trivially satisfied by an empty scan.
    for name in frappe.get_all("Storage Bin", filters={"check_digits": ["in", ("", None)]}, pluck="name"):
        frappe.db.set_value("Storage Bin", name, "check_digits", generate_check_digits())
