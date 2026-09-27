"""Deletes every record of every in-scope WMS Configurator doctype from this site - the
"pull the plug" counterpart to import_profile.py. import_profile.py only ever creates or
updates (by design, so re-running it is always safe), which means it can never clean up a
record a previous, since-corrected profile left behind - exactly the kind of stale rule that
bit us reapplying a fixed preset in production. Wiping first guarantees a clean reinstall.

Run from the bench:

    bench --site <site> execute frappe_wms.setup.wipe_config.wipe_configuration --kwargs "{'confirm': True}"

Pass dry_run=True to see what would be deleted without deleting anything. Mirrors
import_profile.py's APPLY_ORDER (imported from it, so the two doctype lists can't drift apart)
but in reverse, so anything with outgoing Link fields is gone before whatever it might reference.
"""
import frappe

from frappe_wms.setup.import_profile import APPLY_ORDER

SINGLE_DOCTYPES = {"WMS Settings"}
UNRESETTABLE_FIELDTYPES = {"Section Break", "Column Break", "Tab Break", "Table"}


def wipe_configuration(confirm=False, dry_run=False):
    if not confirm and not dry_run:
        frappe.throw("Refusing to wipe without confirm=True (pass dry_run=True first to preview).")

    report = {"deleted": [], "reset": [], "failed": []}

    for doctype in reversed(APPLY_ORDER):
        if doctype in SINGLE_DOCTYPES:
            report["reset"].append(doctype)
            if dry_run: continue
            doc = frappe.get_single(doctype)
            for f in frappe.get_meta(doctype).fields:
                if f.fieldtype in UNRESETTABLE_FIELDTYPES: continue
                doc.set(f.fieldname, 0 if f.fieldtype in ("Check", "Int", "Float", "Currency", "Percent") else None)
            doc.save(ignore_permissions=True)
            continue

        for name in frappe.get_all(doctype, pluck="name"):
            if dry_run:
                report["deleted"].append((doctype, name))
                continue
            frappe.db.savepoint("wms_wipe")
            try:
                frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
                report["deleted"].append((doctype, name))
            except Exception as e:
                frappe.db.rollback(save_point="wms_wipe")
                report["failed"].append((doctype, name, str(e)))

    if not dry_run:
        frappe.db.commit()

    print(f"Deleted: {len(report['deleted'])}, Reset: {len(report['reset'])}, Failed: {len(report['failed'])}")
    for doctype, name, err in report["failed"]:
        print(f"  FAILED {doctype} {name}: {err}")
    return report
