import frappe


def execute():
    # Return Delivery Notes / Purchase Receipts mirrored from WMS receipts/issues were submitted
    # from the mapper-built object, whose status_updater never included ERPNext's return rules
    # (see erpnext_sync._submit_return) - so the original documents' returned_qty was never
    # updated. Re-run ERPNext's own status update for each one so returned_qty/per_returned
    # reflect what has actually come back.
    returns = [("Delivery Note", n) for n in frappe.get_all("Goods Receipt", filters={"docstatus": 1, "erpnext_delivery_note": ["is", "set"]}, pluck="erpnext_delivery_note")]
    returns += [("Purchase Receipt", n) for n in frappe.get_all("Goods Issue", filters={"docstatus": 1, "erpnext_purchase_receipt": ["is", "set"]}, pluck="erpnext_purchase_receipt")]
    for doctype, name in returns:
        if not frappe.db.get_value(doctype, {"name": name, "docstatus": 1, "is_return": 1}):
            continue
        try:
            frappe.get_doc(doctype, name).update_prevdoc_status()
        except Exception:
            # best-effort per document: one odd historical record must not block the migration
            frappe.log_error(title=f"frappe_wms: could not backfill returned quantities for {doctype} {name}")
