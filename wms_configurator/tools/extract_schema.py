#!/usr/bin/env python3
"""Extracts a browser-friendly schema from frappe_wms's DocType JSON files.

Reads the live doctype definitions under frappe_wms/**/doctype/*/*.json (the
source of truth - not the stale doctype_manifest.json at the repo root) and
writes:

  app/schema.json       {doctypes: {name: {...}}, in_scope: [names in wizard order]}
  app/apply_order.json  [doctype names], dependency-sorted, for push.js / import_profile.py

Run from the wms_configurator/ directory:
    python3 tools/extract_schema.py
"""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAPPE_WMS = REPO_ROOT / "frappe_wms"
OUT_DIR = Path(__file__).resolve().parents[1] / "app"

# Top-level (non-child-table) doctypes the configurator manages, in wizard order.
# Anything not listed here (transactional/instance docs, e.g. Goods Receipt,
# Warehouse Task, Handling Unit, WMS Shipment...) is intentionally excluded.
IN_SCOPE_DOCTYPES = [
    "WMS Settings",
    "WMS Warehouse",
    "Storage Type",
    "Storage Section",
    "Bin Type",
    "Activity Area",
    "Storage Bin",
    "Work Center",
    "WMS Stock Type",
    "WMS Movement Type",
    "Warehouse Process Type",
    "Bin Determination Rule",
    "Process Determination Rule",
    "Storage Process",
    "Storage Type Search Sequence",
    "Removal Rule",
    "Warehouse Process Type Determination Rule",
    "WO Creation Rule",
    "Inspection Rule",
    "WMS Exception Code",
    "Count Tolerance Group",
    "Cycle Count Rule",
    "Replenishment Rule",
    "WMS Number Range",
    "WMS HU Number Pool",
    "WMS Print Determination Rule",
    "Handling Unit Type",
    "Packaging Material",
    "Packaging Spec",
    "WMS Route",
    "WMS Resource",
    "WMS Resource Group",
    "Warehouse Queue",
    "Wave Template",
    "Labor Standard",
    "Billing Rate",
]

# Day-to-day (transactional) doctypes. Never edited in a profile - they're only read here so the
# "How it fits together" map can show which configuration feeds them. Only their Link fields
# (including links inside child tables) are recorded.
RUNTIME_DOCTYPES = [
    "Inbound Delivery", "Goods Receipt", "WMS Quality Inspection",
    "Handling Unit", "WMS Product", "WMS Product Warehouse",
    "Warehouse Request", "Warehouse Order", "Warehouse Task", "WMS Print Spool",
    "Consolidation Group", "Kitting Order",
    "Outbound Delivery", "Stock Allocation", "WMS Wave", "Packing Order", "VAS Order",
    "Goods Issue", "WMS Shipment",
    "WMS Stock Balance", "WMS Stock Ledger Entry", "WMS Physical Inventory Count",
]

# Doctypes referenced from ERPNext / frappe core, not created by the configurator.
# Kept in field metadata (so Link inputs still know what they point at) but never
# added to the dependency graph or the doctypes we try to create.
EXTERNAL_DOCTYPES = {"Item", "Item Group", "Company", "Warehouse", "User", "Role"}

FIELD_KEYS = [
    "fieldname", "label", "fieldtype", "options", "reqd", "default",
    "depends_on", "description", "precision", "read_only",
]


def find_doctype_json(name):
    scrubbed = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    matches = list(FRAPPE_WMS.glob(f"*/doctype/{scrubbed}/{scrubbed}.json"))
    if not matches:
        raise FileNotFoundError(f"No doctype json found for {name!r} (looked for {scrubbed}.json)")
    if len(matches) > 1:
        raise ValueError(f"Multiple doctype json files found for {name!r}: {matches}")
    return matches[0]


def load_doctype(name):
    path = find_doctype_json(name)
    return json.loads(path.read_text())


def extract_fields(doc):
    fields = []
    for f in doc.get("fields", []):
        if f.get("fieldtype") in ("Section Break", "Column Break", "Tab Break"):
            continue
        entry = {k: f.get(k) for k in FIELD_KEYS if f.get(k) not in (None, "", 0)}
        entry["fieldname"] = f["fieldname"]
        entry["fieldtype"] = f["fieldtype"]
        entry.setdefault("label", f["fieldname"].replace("_", " ").title())
        fields.append(entry)
    return fields


def key_strategy(doc):
    """How to identify an existing record on a target site for upsert purposes."""
    autoname = doc.get("autoname") or ""
    if autoname.startswith("field:"):
        return {"type": "field", "fields": [autoname.split(":", 1)[1]]}
    if autoname.startswith("format:"):
        fields = re.findall(r"\{([a-zA-Z0-9_]+)\}", autoname)
        return {"type": "format", "format": autoname.split(":", 1)[1], "fields": fields}
    if doc.get("issingle"):
        return {"type": "single"}
    # hash / counter autoname (e.g. "BDR-.#####") or Prompt: no natural key.
    # Import treats these as insert-if-not-an-exact-duplicate.
    return {"type": "none"}


def build_doctype_entry(name, visited, child_doctypes):
    doc = load_doctype(name)
    fields = extract_fields(doc)
    for f in fields:
        if f["fieldtype"] == "Table" and f.get("options"):
            child_doctypes.add(f["options"])
    entry = {
        "name": doc["name"],
        "module": doc.get("module"),
        "issingle": bool(doc.get("issingle")),
        "istable": bool(doc.get("istable")),
        "description": doc.get("description", ""),
        "fields": fields,
        "key": key_strategy(doc),
    }
    return entry


def dependency_edges(doctypes):
    """{(from, to): required} meaning `to` should exist before `from`.

    Links inside child tables count towards their parent. `required` is True when the
    Link field is mandatory; optional links may be applied in a second pass instead.
    """
    names = set(doctypes)
    edges = {}

    def add(frm, to, required):
        if not to or to == frm or to not in names:
            return  # external doctype or self-link: handled at apply time, not here
        edges[(frm, to)] = edges.get((frm, to), False) or required

    for name, entry in doctypes.items():
        for f in entry["fields"]:
            if f["fieldtype"] == "Link":
                add(name, f.get("options"), bool(f.get("reqd")))
            elif f["fieldtype"] == "Table" and f.get("options") in doctypes:
                for cf in doctypes[f["options"]]["fields"]:
                    if cf["fieldtype"] == "Link":
                        add(name, cf.get("options"), bool(cf.get("reqd")))
    return edges


def topological_order(names, edges):
    """Dependency order that respects every required link, and optional links wherever possible.

    Optional links can form cycles (WMS Warehouse -> default bin -> Storage Bin -> WMS Warehouse).
    When only such a cycle is left, the node whose *required* links are all satisfied and that the most
    other nodes wait on (WMS Warehouse) goes first; its open optional links are then filled in by
    a second pass at apply time (see forward_links()).
    """
    deps = {n: {} for n in names}
    for (frm, to), required in edges.items():
        deps[frm][to] = required
    placed, ordered = set(), []
    remaining = set(names)
    while remaining:
        ready = sorted(n for n in remaining if all(d in placed for d in deps[n]))
        if not ready:
            ok = sorted(n for n in remaining if all(d in placed for d, req in deps[n].items() if req))
            pool = ok or sorted(remaining)
            def dependents(n):
                return sum(1 for m in remaining if n in deps[m])
            ready = [min(pool, key=lambda n: (-dependents(n), sum(1 for d in deps[n] if d not in placed), n))]
        for n in ready:
            ordered.append(n)
            placed.add(n)
            remaining.discard(n)
    return ordered


def runtime_links(name):
    doc = load_doctype(name)
    links = []
    for f in doc.get("fields", []):
        if f.get("fieldtype") == "Link" and f.get("options"):
            links.append({"target": f["options"], "label": f.get("label") or f["fieldname"]})
        elif f.get("fieldtype") == "Table" and f.get("options"):
            for cf in load_doctype(f["options"]).get("fields", []):
                if cf.get("fieldtype") == "Link" and cf.get("options"):
                    links.append({"target": cf["options"], "label": f"{f.get('label') or f['fieldname']} → {cf.get('label') or cf['fieldname']}"})
    return {"module": doc.get("module"), "links": links}


def main():
    child_doctypes = set()
    doctypes = {}
    for name in IN_SCOPE_DOCTYPES:
        doctypes[name] = build_doctype_entry(name, set(), child_doctypes)

    child_doctypes -= set(IN_SCOPE_DOCTYPES)
    for name in sorted(child_doctypes):
        doctypes[name] = build_doctype_entry(name, set(), child_doctypes)

    edges = dependency_edges(doctypes)
    edges = {k: v for k, v in edges.items() if k[0] in IN_SCOPE_DOCTYPES and k[1] in IN_SCOPE_DOCTYPES}
    apply_order = topological_order(IN_SCOPE_DOCTYPES, edges)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    schema = {
        "generated_from": "frappe_wms doctype JSON",
        "in_scope": IN_SCOPE_DOCTYPES,
        "external_doctypes": sorted(EXTERNAL_DOCTYPES),
        "doctypes": doctypes,
        "runtime": {n: runtime_links(n) for n in RUNTIME_DOCTYPES},
    }
    (OUT_DIR / "schema.json").write_text(json.dumps(schema, indent=1, sort_keys=False))
    (OUT_DIR / "apply_order.json").write_text(json.dumps(apply_order, indent=1))

    print(f"Wrote {OUT_DIR / 'schema.json'} ({len(doctypes)} doctypes, {len(child_doctypes)} child tables)")
    print(f"Wrote {OUT_DIR / 'apply_order.json'} ({len(apply_order)} entries)")
    missing = [n for n in IN_SCOPE_DOCTYPES if n not in apply_order]
    if missing:
        print("WARNING: missing from apply_order:", missing)


if __name__ == "__main__":
    main()
