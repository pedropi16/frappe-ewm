import frappe
from frappe import _

# SAP EWM assigns HU/document numbers from configured number ranges, most-specific match
# wins (warehouse+HU type, then HU type, then warehouse, then a global fallback). Mirrors the
# same "narrower rule wins" pattern used by services/determination.py's rule tables.
def _candidate_filters(warehouse, hu_type):
    combos = []
    if warehouse and hu_type:
        combos.append({"warehouse": warehouse, "hu_type": hu_type})
    if hu_type:
        combos.append({"warehouse": ["in", ["", None]], "hu_type": hu_type})
    if warehouse:
        combos.append({"warehouse": warehouse, "hu_type": ["in", ["", None]]})
    combos.append({"warehouse": ["in", ["", None]], "hu_type": ["in", ["", None]]})
    return combos


def find_number_range(range_for, warehouse=None, hu_type=None):
    for combo in _candidate_filters(warehouse, hu_type):
        name = frappe.db.get_value("WMS Number Range", {"range_for": range_for, "active": 1, **combo}, "name")
        if name:
            return name
    return None


def _warehouse_of(doc):
    warehouse = doc.get("warehouse")
    if warehouse or doc.doctype != "Packing Order":
        return warehouse
    # Packing Order has no warehouse field of its own - derive it the same way the rest of the
    # app does, from the delivery it's packing for or, failing that, its work center bin.
    if doc.outbound_delivery:
        return frappe.db.get_value("Outbound Delivery", doc.outbound_delivery, "warehouse")
    if doc.work_center_bin:
        return frappe.db.get_value("Storage Bin", doc.work_center_bin, "warehouse")
    return None


def autoname_from_range(doc, method=None):
    # A single hook, wired in hooks.py's doc_events for every document type that should be
    # numberable this way: if a WMS Number Range is configured for this doctype (optionally
    # scoped to a warehouse), use it. Otherwise leave doc.name untouched so Frappe falls back to
    # that doctype's ordinary naming-series autoname - configuring a range is opt-in per doctype,
    # nothing breaks for one that was never given one.
    warehouse = _warehouse_of(doc)
    if find_number_range(doc.doctype, warehouse=warehouse):
        doc.name = next_number(doc.doctype, warehouse=warehouse)


def next_number(range_for, warehouse=None, hu_type=None):
    range_name = find_number_range(range_for, warehouse, hu_type)
    if not range_name:
        frappe.throw(_("No active Number Range is configured for {0}").format(range_for))
    # Row-lock so concurrent RF scans/shipment creation never hand out the same number twice.
    frappe.db.sql("select name from `tabWMS Number Range` where name=%s for update", range_name)
    # A reusable HU (services/handling_unit.recycle_handling_unit) frees its number back into this
    # range's pool - SAP EWM reissues those before ever incrementing further.
    pooled = frappe.db.get_value("WMS HU Number Pool", {"number_range": range_name}, ["name", "hu_number"], as_dict=True, order_by="creation asc")
    if pooled:
        frappe.delete_doc("WMS HU Number Pool", pooled.name, ignore_permissions=True)
        return pooled.hu_number
    doc = frappe.get_doc("WMS Number Range", range_name)
    next_value = max(doc.current_number or 0, doc.start_number - 1) + 1
    if next_value > doc.end_number:
        frappe.throw(_("Number Range {0} is exhausted").format(range_name))
    doc.db_set("current_number", next_value, update_modified=False)
    return f"{doc.prefix or ''}{str(next_value).zfill(doc.number_length or 0)}"
