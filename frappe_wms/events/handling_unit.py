import frappe
from frappe import _

def validate_hu(doc, method=None):
    if doc.parent_hu == doc.name and doc.name:
        frappe.throw(_("A handling unit cannot contain itself"))
    if doc.parent_hu:
        parent = frappe.get_cached_doc("Handling Unit", doc.parent_hu)
        if parent.warehouse != doc.warehouse or parent.current_bin != doc.current_bin:
            frappe.throw(_("Parent and child handling units must be in the same warehouse and bin"))
        ancestor = parent
        visited = {doc.name}
        while ancestor and ancestor.name:
            if ancestor.name in visited:
                frappe.throw(_("Circular handling-unit hierarchy detected"))
            visited.add(ancestor.name)
            if not ancestor.parent_hu:
                break
            ancestor = frappe.get_cached_doc("Handling Unit", ancestor.parent_hu)
        doc.top_hu = ancestor.name
        doc.hierarchy_level = (parent.hierarchy_level or 0) + 1
    else:
        doc.top_hu = doc.name
        doc.hierarchy_level = 0
    if doc.loaded and doc.status not in {"Loaded", "Shipped"}:
        frappe.throw(_("A loaded handling unit must have Loaded or Shipped status"))

def on_hu_update(doc, method=None):
    if doc.flags.in_insert:
        return
    if doc.has_value_changed("parent_hu") and not doc.flags.get("wms_service_update"):
        frappe.throw(_("Change HU hierarchy through the packing service"))
