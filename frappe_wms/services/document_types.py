"""WMS Document Types (SAP EWM document types: number range, status profile, field control, defaults).

A document type belongs to a category (Inbound Delivery, Outbound Delivery, Warehouse Request, Delivery Request, Final Outbound Delivery) and
optionally a warehouse. A document names its type in `document_type`; one without gets the category's default type. Saving a document applies the type:
  * Field Defaults fill blank fields of a new document;
  * Field Control: Required is enforced, Read Only blocks a change after creation (Hidden / Required / Read Only also shape the form, see
    public/js/wms_document_type.js);
  * Status Profile: when the status changes only the listed transitions are allowed (optionally per role);
  * the type's Number Range names the document (services/numbering.autoname_from_range).
"""
import frappe
from frappe import _

CATEGORY = {"Inbound Delivery": "Inbound Delivery", "Outbound Delivery": "Outbound Delivery", "Warehouse Request": "Warehouse Request",
            "WMS Delivery Request": "Delivery Request", "Final Outbound Delivery": "Final Outbound Delivery"}


def default_type(doctype, warehouse=None):
    for filters in (({"warehouse": warehouse} if warehouse else None), {"warehouse": ["in", ["", None]]}):
        if filters is None: continue
        name = frappe.db.get_value("WMS Document Type", {"category": CATEGORY[doctype], "is_default": 1, "active": 1, **filters})
        if name: return name
    return None


def validate_document_type(doc, method=None):
    if doc.doctype not in CATEGORY: return
    if not doc.get("document_type"):
        doc.document_type = default_type(doc.doctype, doc.get("warehouse"))
        if not doc.document_type: return
    dt = frappe.get_cached_doc("WMS Document Type", doc.document_type)
    if not dt.active: frappe.throw(_("Document type {0} is not active").format(dt.name))
    if dt.category != CATEGORY[doc.doctype]: frappe.throw(_("Document type {0} is for {1}, not {2}").format(dt.name, dt.category, doc.doctype))
    if dt.warehouse and doc.get("warehouse") and dt.warehouse != doc.warehouse: frappe.throw(_("Document type {0} is for warehouse {1}").format(dt.name, dt.warehouse))
    if doc.is_new():
        for row in dt.defaults:
            if not doc.get(row.fieldname) and doc.meta.has_field(row.fieldname): doc.set(row.fieldname, row.value)
    for row in dt.field_control:
        label = doc.meta.get_label(row.fieldname) or row.fieldname
        if row.control == "Required" and not doc.get(row.fieldname): frappe.throw(_("{0} is required for document type {1}").format(label, dt.name))
        if row.control == "Read Only" and not doc.is_new() and doc.has_value_changed(row.fieldname): frappe.throw(_("{0} cannot be changed for document type {1}").format(label, dt.name))
    if dt.status_transitions and not doc.is_new() and doc.meta.has_field("status") and doc.has_value_changed("status"):
        before = doc.get_doc_before_save()
        old, new = (before.status if before else None), doc.status
        allowed = [t for t in dt.status_transitions if t.from_status == old and t.to_status == new]
        if not allowed: frappe.throw(_("Document type {0} does not allow the status change {1} -> {2}").format(dt.name, old, new))
        if not any(not t.allowed_role or t.allowed_role in frappe.get_roles() for t in allowed) and "System Manager" not in frappe.get_roles():
            frappe.throw(_("The status change {0} -> {1} needs the role {2}").format(old, new, allowed[0].allowed_role))


@frappe.whitelist()
def field_controls(doctype, document_type=None, warehouse=None):
    """For the form script: the Field Control rows that apply to a document."""
    document_type = document_type or default_type(doctype, warehouse)
    if not document_type: return []
    return [{"fieldname": r.fieldname, "control": r.control} for r in frappe.get_cached_doc("WMS Document Type", document_type).field_control]
