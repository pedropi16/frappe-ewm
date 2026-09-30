import frappe
from frappe.utils import flt


def execute():
    # Before inventory_count._mirror_posted_rows, a count only reached ERPNext once every line was
    # resolved - lines already posted to the WMS ledger (within tolerance) stayed out of ERPNext
    # while the rest waited for a recount/approval, and for good if the count was then cancelled.
    # Mirror those lines now for any count that never got an ERPNext entry at all.
    from frappe_wms.services.inventory_count import _mirror_posted_rows
    names = frappe.get_all("WMS Physical Inventory Count",
        filters={"status": ["in", ["Under Review", "Cancelled", "Counting", "Counted"]],
                 "erpnext_gain_stock_entry": ["in", ["", None]], "erpnext_loss_stock_entry": ["in", ["", None]]}, pluck="name")
    for name in names:
        doc = frappe.get_doc("WMS Physical Inventory Count", name)
        rows = [r for r in doc.items if r.status == "Posted" and flt(r.variance)]
        if not rows: continue
        try:
            _mirror_posted_rows(doc, rows)
            doc.db_set({"erpnext_gain_stock_entry": doc.erpnext_gain_stock_entry, "erpnext_loss_stock_entry": doc.erpnext_loss_stock_entry}, update_modified=False)
        except Exception:
            frappe.log_error(title=f"frappe_wms: could not mirror posted lines of count {name} to ERPNext")
