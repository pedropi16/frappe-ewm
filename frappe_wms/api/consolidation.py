import frappe
from frappe_wms.services.consolidation import (
    create_consolidation_group as _create_consolidation_group,
    set_consolidation_target_hu as _set_consolidation_target_hu,
    find_joinable_references as _find_joinable_references,
    add_consolidation_line as _add_consolidation_line,
    remove_consolidation_line as _remove_consolidation_line,
    gather_consolidation_group as _gather_consolidation_group,
    complete_consolidation_group as _complete_consolidation_group,
)
from frappe_wms.services.task import my_resource


@frappe.whitelist()
def list_open_consolidation_groups(user=None):
    resource = my_resource(user)
    filters = {"status": ["in", ("Draft", "Open")]}
    if resource: filters["warehouse"] = resource.warehouse
    groups = frappe.get_list("Consolidation Group", filters=filters,
        fields=["name", "warehouse", "staging_bin", "target_hu", "status", "gather_status", "priority", "creation"],
        order_by="creation asc", limit=50)
    for group in groups:
        rows = frappe.get_all("Consolidation Group Line", filters={"parent": group.name}, fields=["status"])
        group["line_count"] = len(rows)
        group["done_count"] = sum(1 for r in rows if r.status in ("Gathered", "Deconsolidated"))
    return groups


@frappe.whitelist()
def get_consolidation_group(group_name):
    doc = frappe.get_doc("Consolidation Group", group_name)
    doc.check_permission("read")
    return doc.as_dict()


@frappe.whitelist()
def create_consolidation_group(warehouse, staging_bin, priority=None, target_hu=None):
    return _create_consolidation_group(warehouse, staging_bin, priority, target_hu)


@frappe.whitelist()
def set_consolidation_target_hu(group_name, target_hu=None, hu_type=None):
    return _set_consolidation_target_hu(group_name, target_hu, hu_type)


@frappe.whitelist()
def find_joinable_references(barcode):
    return _find_joinable_references(barcode)


@frappe.whitelist()
def add_consolidation_line(group_name, reference_doctype, reference_name):
    return _add_consolidation_line(group_name, reference_doctype, reference_name)


@frappe.whitelist()
def remove_consolidation_line(group_name, line_name):
    return _remove_consolidation_line(group_name, line_name)


@frappe.whitelist()
def gather_consolidation_group(group_name):
    return _gather_consolidation_group(group_name)


@frappe.whitelist()
def complete_consolidation_group(group_name):
    return _complete_consolidation_group(group_name)
