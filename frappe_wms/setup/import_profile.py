"""Applies a WMS Configurator profile (exported JSON) to this site.

Companion to wms_configurator/app/js/push.js - the two apply the same records
in the same dependency order, one over REST, this one directly via the ORM.
Run from the bench:

    bench --site <site> execute frappe_wms.setup.import_profile.import_profile \\
        --kwargs "{'path': '/path/to/your-profile.wms-profile.json'}"

Deliberately derives autoname/field metadata from frappe.get_meta() at
runtime rather than reading wms_configurator/app/schema.json, so this keeps
working even when run against a bench where the configurator's source tree
isn't checked out alongside frappe_wms.
"""
import json
import re

import frappe

# Mirrors wms_configurator/tools/extract_schema.py's IN_SCOPE_DOCTYPES, pre-sorted into
# dependency order (apply_order.json) - regenerate this list from that file if the
# configurator's scope changes. A few optional links point "forward" (WMS Warehouse's default
# bins -> Storage Bin, which itself needs the warehouse); those are filled in by a second pass.
APPLY_ORDER = [
    "Bin Type", "Handling Unit Type", "WMS Exception Code", "WMS Movement Type",
    "WMS Stock Type", "Packaging Material", "WMS Settings", "Warehouse Process Type",
    "Packaging Spec", "WMS Warehouse", "Activity Area", "Billing Rate",
    "Count Tolerance Group", "Inspection Rule", "Labor Standard", "Storage Type",
    "WMS Number Range", "WMS Resource Group", "WO Creation Rule",
    "Warehouse Process Type Determination Rule", "Cycle Count Rule", "Storage Section",
    "Storage Type Search Sequence", "WMS HU Number Pool", "Warehouse Queue",
    "Storage Bin", "Bin Determination Rule", "Removal Rule", "Replenishment Rule",
    "Storage Process", "WMS Route", "Work Center", "Process Determination Rule",
    "WMS Resource", "Wave Template", "WMS Print Determination Rule",
]


def _key_strategy(doctype):
    meta = frappe.get_meta(doctype)
    if meta.issingle:
        return {"type": "single"}
    autoname = meta.autoname or ""
    if autoname.startswith("field:"):
        return {"type": "field", "fields": [autoname.split(":", 1)[1]]}
    if autoname.startswith("format:"):
        fmt = autoname.split(":", 1)[1]
        return {"type": "format", "format": fmt, "fields": re.findall(r"\{([a-zA-Z0-9_]+)\}", fmt)}
    return {"type": "none"}


def _compute_name(doctype, record, key):
    if key["type"] == "single":
        return doctype
    if key["type"] == "field":
        return record.get(key["fields"][0]) or ""
    if key["type"] == "format":
        return re.sub(r"\{([a-zA-Z0-9_]+)\}", lambda m: str(record.get(m.group(1)) or ""), key["format"])
    return ""


def _plain_fieldnames(doctype):
    meta = frappe.get_meta(doctype)
    skip = {"Table", "Section Break", "Column Break", "Tab Break"}
    return [f.fieldname for f in meta.fields if f.fieldtype not in skip]


def _self_link_fieldnames(doctype):
    meta = frappe.get_meta(doctype)
    return [f.fieldname for f in meta.fields if f.fieldtype == "Link" and f.options == doctype]


def _sort_self_referential(doctype, records, key):
    self_fields = _self_link_fieldnames(doctype)
    if not self_fields:
        return records
    by_name = {_compute_name(doctype, r, key): r for r in records}
    placed, ordered, remaining = set(), [], list(records)
    for _ in range(len(records) + 1):
        if not remaining:
            break
        ready = [r for r in remaining if all((r.get(f) not in by_name) or (r.get(f) in placed) for f in self_fields)]
        if not ready:
            ordered.extend(remaining)
            break
        for r in ready:
            placed.add(_compute_name(doctype, r, key))
        ordered.extend(ready)
        remaining = [r for r in remaining if r not in ready]
    return ordered


def _forward_link_fields(doctype):
    """Top-level Link fields of `doctype` that point at a doctype applied later."""
    pos = {d: i for i, d in enumerate(APPLY_ORDER)}
    here = pos[doctype]
    return [
        f.fieldname
        for f in frappe.get_meta(doctype).fields
        if f.fieldtype == "Link" and pos.get(f.options, -1) > here
    ]


def import_profile(path, dry_run=False):
    """Reads an exported WMS Configurator profile and applies it to this site."""
    with open(path) as fh:
        profile = json.load(fh)

    records_by_doctype = profile.get("records", {})
    report = {"created": [], "updated": [], "skipped": [], "failed": []}

    none_key_cache = {}
    deferred = []  # (doctype, name, {forward link fields}) filled in once everything exists

    for doctype in APPLY_ORDER:
        records = records_by_doctype.get(doctype) or []
        if not records:
            continue
        key = _key_strategy(doctype)
        records = _sort_self_referential(doctype, records, key)

        if key["type"] == "single":
            data = records[0]
            if dry_run:
                report["updated"].append((doctype, doctype))
                continue
            frappe.db.savepoint("wms_import")
            try:
                doc = frappe.get_single(doctype)
                doc.update(data)
                doc.save(ignore_permissions=True)
                report["updated"].append((doctype, doctype))
            except Exception as e:
                frappe.db.rollback(save_point="wms_import")
                report["failed"].append((doctype, doctype, str(e)))
            continue

        if key["type"] == "none":
            if doctype not in none_key_cache:
                none_key_cache[doctype] = frappe.get_all(doctype, fields=["*"], limit_page_length=0)
            plain_fields = _plain_fieldnames(doctype)
            for record in records:
                label = record.get("priority") and f"{doctype} (priority {record['priority']})" or doctype
                is_duplicate = any(
                    all(str(existing.get(f) or "") == str(record.get(f) or "") for f in plain_fields)
                    for existing in none_key_cache[doctype]
                )
                if is_duplicate:
                    report["skipped"].append((doctype, label))
                    continue
                if dry_run:
                    report["created"].append((doctype, label))
                    continue
                frappe.db.savepoint("wms_import")
                try:
                    doc = frappe.get_doc({"doctype": doctype, **record})
                    doc.insert(ignore_permissions=True)
                    report["created"].append((doctype, doc.name))
                except Exception as e:
                    frappe.db.rollback(save_point="wms_import")
                    report["failed"].append((doctype, label, str(e)))
            continue

        # field / format key: upsert by computed name
        forward = _forward_link_fields(doctype)
        for full_record in records:
            record = {k: v for k, v in full_record.items() if k not in forward}
            later = {k: full_record[k] for k in forward if full_record.get(k)}
            if doctype == "WMS Warehouse" and not record.get("company"):
                companies = frappe.get_all("Company", pluck="name", limit=2)
                if len(companies) == 1:  # presets can't know the company; a single-company site has no choice to make
                    record["company"] = companies[0]
            name = _compute_name(doctype, full_record, key)
            if dry_run:
                report["updated" if name and frappe.db.exists(doctype, name) else "created"].append((doctype, name))
                continue
            frappe.db.savepoint("wms_import")
            try:
                if name and frappe.db.exists(doctype, name):
                    doc = frappe.get_doc(doctype, name)
                    doc.update(record)
                    doc.save(ignore_permissions=True)
                    report["updated"].append((doctype, name))
                else:
                    doc = frappe.get_doc({"doctype": doctype, **record})
                    doc.insert(ignore_permissions=True)
                    report["created"].append((doctype, doc.name))
                if later:
                    deferred.append((doctype, doc.name, later))
            except Exception as e:
                frappe.db.rollback(save_point="wms_import")  # undo only this record, keep the rest
                report["failed"].append((doctype, name, str(e)))

    for doctype, name, later in deferred:
        frappe.db.savepoint("wms_import")
        try:
            doc = frappe.get_doc(doctype, name)
            doc.update(later)
            doc.save(ignore_permissions=True)
        except Exception as e:
            frappe.db.rollback(save_point="wms_import")
            report["failed"].append((doctype, name, f"second pass (forward links): {e}"))

    if not dry_run:
        frappe.db.commit()

    print(f"Created: {len(report['created'])}, Updated: {len(report['updated'])}, "
          f"Skipped (already present): {len(report['skipped'])}, Failed: {len(report['failed'])}")
    for doctype, name, err in report["failed"]:
        print(f"  FAILED {doctype} {name}: {err}")
    return report
