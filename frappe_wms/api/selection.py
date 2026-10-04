"""WMS Monitor selection screens: one generic search endpoint per monitor view, plus saved
selection variants and personal layouts (SAP Business Client's "Save as Variant" and
"Change Layout"). See services/selection.py for the criteria format."""
import json

import frappe
from frappe import _
from frappe.utils import cint

from frappe_wms.services.concurrency import retry_on_deadlock
from frappe_wms.services.selection import run_selection, selectable_fields
from frappe_wms.utils import require_wms_access


def _item_virtuals(product_column):
    return {
        "item_name": {"label": "Product Description", "column": "`sel_item`.`item_name`",
                      "sql": f"{product_column} in (select `sel_item`.name from `tabItem` `sel_item` where {{cond}})"},
        "item_group": {"label": "Product Group", "fieldtype": "Link", "options": "Item Group", "column": "`sel_item`.`item_group`",
                       "sql": f"{product_column} in (select `sel_item`.name from `tabItem` `sel_item` where {{cond}})"},
    }


def _bin_virtual(bin_column, label="Storage Type"):
    return {"label": label, "fieldtype": "Link", "options": "Storage Type", "column": "`sel_bin`.`storage_type`",
            "sql": f"{bin_column} in (select `sel_bin`.name from `tabStorage Bin` `sel_bin` where {{cond}})"}


# Every (bin, delivery, sales order, route, wave) a bin is tied to: stock allocated to a delivery sits in
# the allocation's bin, and picked / packed stock sits in an HU that carries the delivery.
_DELIVERY_BINS = """
    select a.storage_bin, a.outbound_delivery, i.sales_order, d.route, (select w.parent from `tabWMS Wave Delivery` w where w.outbound_delivery = d.name limit 1) as wave
      from `tabStock Allocation` a join `tabOutbound Delivery` d on d.name = a.outbound_delivery
      left join `tabOutbound Delivery Item` i on i.name = a.outbound_delivery_item
     where a.status in ('Allocated', 'Partially Picked', 'Picked')
    union
    select h.current_bin, h.outbound_delivery, i.sales_order, d.route, (select w.parent from `tabWMS Wave Delivery` w where w.outbound_delivery = d.name limit 1) as wave
      from `tabHandling Unit` h join `tabOutbound Delivery` d on d.name = h.outbound_delivery
      left join `tabOutbound Delivery Item` i on i.parent = d.name
"""

# view key -> DocType, default selection fields, default columns, order, virtual fields.
VIEWS = {
    "inbound": {
        "doctype": "Inbound Delivery", "title": "Inbound Deliveries",
        "selection": ["inbound_delivery_number", "status", "supplier", "receiving_bin", "receipt_status", "process_status", "expected_arrival", "item"],
        "columns": ["name", "inbound_delivery_number", "supplier", "receiving_bin", "expected_arrival", "posting_date",
                    "receipt_status", "process_status", "status", "external_reference", "modified"],
        "virtual": {
            "item": {"label": "Product", "fieldtype": "Link", "options": "Item", "column": "`sel_line`.`item`",
                     "sql": "`tabInbound Delivery`.name in (select `sel_line`.parent from `tabInbound Delivery Item` `sel_line` where {cond})"},
            "purchase_order": {"label": "Purchase Order", "fieldtype": "Link", "options": "Purchase Order", "column": "`sel_line`.`purchase_order`",
                               "sql": "`tabInbound Delivery`.name in (select `sel_line`.parent from `tabInbound Delivery Item` `sel_line` where {cond})"},
        },
    },
    "outbound": {
        "doctype": "Outbound Delivery", "title": "Outbound Deliveries",
        "selection": ["outbound_delivery_number", "status", "customer", "route", "delivery_date", "priority", "allocation_status", "item"],
        "columns": ["name", "outbound_delivery_number", "customer", "route", "delivery_date", "priority", "staging_bin", "door",
                    "allocation_status", "picking_status", "packing_status", "loading_status", "goods_issue_status", "status",
                    "external_reference", "modified"],
        "virtual": {
            "item": {"label": "Product", "fieldtype": "Link", "options": "Item", "column": "`sel_line`.`item`",
                     "sql": "`tabOutbound Delivery`.name in (select `sel_line`.parent from `tabOutbound Delivery Item` `sel_line` where {cond})"},
            "sales_order": {"label": "Sales Order", "fieldtype": "Link", "options": "Sales Order", "column": "`sel_line`.`sales_order`",
                            "sql": "`tabOutbound Delivery`.name in (select `sel_line`.parent from `tabOutbound Delivery Item` `sel_line` where {cond})"},
            "wave": {"label": "Wave", "fieldtype": "Link", "options": "WMS Wave", "column": "`sel_wave`.`parent`",
                     "sql": "`tabOutbound Delivery`.name in (select `sel_wave`.outbound_delivery from `tabWMS Wave Delivery` `sel_wave` where {cond})"},
        },
    },
    "waves": {
        "doctype": "WMS Wave", "title": "Waves",
        "selection": ["name", "status", "route", "ship_date", "released_by"],
        "columns": ["name", "route", "ship_date", "priority", "picking_strategy", "status", "released_at", "released_by", "modified"],
        "virtual": {},
    },
    "stock": {
        "doctype": "WMS Stock Balance", "title": "Stock Overview", "order_by": "`tabWMS Stock Balance`.storage_bin asc, `tabWMS Stock Balance`.product asc",
        "selection": ["product", "storage_bin", "storage_type", "stock_type", "handling_unit", "batch_no", "quantity", "item_group"],
        "default_criteria": {"quantity": {"exclude": [{"op": "eq", "low": 0}]}},
        # The grouped presentation (storage type / allocation) needs these whatever layout is saved.
        "required": ["product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type", "quantity",
                     "allocated_quantity", "available_quantity", "stock_uom", "first_receipt_date", "last_movement_date"],
        "columns": ["product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type", "quantity",
                    "allocated_quantity", "available_quantity", "stock_uom", "shelf_life_expiry_date", "last_movement_date"],
        "virtual": {"storage_type": _bin_virtual("`tabWMS Stock Balance`.storage_bin"), **_item_virtuals("`tabWMS Stock Balance`.product")},
    },
    # The Packing Center: searches Storage Bins (what the tree is rooted at), by the bin itself or by
    # what is in it - an HU, an HU type or packing material, a product, a batch, a serial number.
    "packing": {
        "doctype": "Storage Bin", "title": "Packing Center", "order_by": "`tabStorage Bin`.name asc",
        "labels": {"name": "Storage Bin"},
        "selection": ["name", "work_center", "storage_type", "storage_section", "handling_unit", "hu_type", "packaging_material", "contains_product", "contains_batch",
                      "contains_serial", "outbound_delivery", "sales_order", "route", "wave"],
        "columns": ["name"],  # the tree's own columns are a catalog on the client; this only finds the bins
        "virtual": {
            "handling_unit": {"label": "Handling Unit", "fieldtype": "Link", "options": "Handling Unit", "column": "`sel_hu`.`name`",
                              "sql": "`tabStorage Bin`.name in (select `sel_hu`.current_bin from `tabHandling Unit` `sel_hu` where {cond})"},
            "hu_type": {"label": "HU Type", "fieldtype": "Link", "options": "Handling Unit Type", "column": "`sel_hu`.`hu_type`",
                        "sql": "`tabStorage Bin`.name in (select `sel_hu`.current_bin from `tabHandling Unit` `sel_hu` where {cond})"},
            "packaging_material": {"label": "Packing Material", "fieldtype": "Link", "options": "Packaging Material", "column": "`sel_hu`.`packaging_material`",
                                   "sql": "`tabStorage Bin`.name in (select `sel_hu`.current_bin from `tabHandling Unit` `sel_hu` where {cond})"},
            "contains_product": {"label": "Contains Product", "fieldtype": "Link", "options": "Item", "column": "`sel_bal`.`product`",
                                 "sql": "`tabStorage Bin`.name in (select `sel_bal`.storage_bin from `tabWMS Stock Balance` `sel_bal` where `sel_bal`.quantity > 0 and {cond})"},
            "contains_batch": {"label": "Contains Batch", "fieldtype": "Link", "options": "Batch", "column": "`sel_bal`.`batch_no`",
                               "sql": "`tabStorage Bin`.name in (select `sel_bal`.storage_bin from `tabWMS Stock Balance` `sel_bal` where `sel_bal`.quantity > 0 and {cond})"},
            "work_center": {"label": "Work Center", "fieldtype": "Link", "options": "Work Center", "column": "`sel_wc`.`name`",
                            "sql": "`tabStorage Bin`.name in (select `sel_wc`.bin from `tabWork Center` `sel_wc` where {cond})"},
            **{field: {"label": label, "fieldtype": "Link", "options": options, "column": f"`sel_doc`.`{field}`",
                       "sql": "`tabStorage Bin`.name in (select `sel_doc`.storage_bin from (" + _DELIVERY_BINS + ") `sel_doc` where {cond})"}
               for field, label, options in (("outbound_delivery", "Document (Outbound Delivery)", "Outbound Delivery"), ("sales_order", "Sales Order", "Sales Order"),
                                             ("route", "Route", "WMS Route"), ("wave", "Wave", "WMS Wave"))},
            "contains_serial": {"label": "Contains Serial No", "fieldtype": "Link", "options": "Serial No", "column": "`sel_bal`.`serial_no`",
                                "sql": "`tabStorage Bin`.name in (select `sel_bal`.storage_bin from `tabWMS Stock Balance` `sel_bal` where `sel_bal`.quantity > 0 and {cond})"},
        },
    },
    "tasks": {
        "doctype": "Warehouse Task", "title": "Warehouse Tasks",
        "selection": ["name", "task_type", "status", "product", "source_bin", "destination_bin", "assigned_resource", "warehouse_order", "queue", "wave", "confirmed_at"],
        "columns": ["name", "task_type", "product", "planned_quantity", "confirmed_quantity", "stock_uom", "batch_no", "serial_no",
                    "source_bin", "destination_bin", "source_hu", "destination_hu", "priority", "status", "assigned_resource",
                    "wave", "queue", "warehouse_order", "confirmed_at", "confirmed_by", "exception_code", "modified"],
        "virtual": {"source_storage_type": _bin_virtual("`tabWarehouse Task`.source_bin", "Source Storage Type"),
                    "destination_storage_type": _bin_virtual("`tabWarehouse Task`.destination_bin", "Destination Storage Type"),
                    **_item_virtuals("`tabWarehouse Task`.product")},
    },
    "warehouse_orders": {
        "doctype": "Warehouse Order", "title": "Warehouse Orders",
        "selection": ["name", "activity", "queue", "status", "assigned_resource", "priority", "wave", "reference_doctype", "reference_name"],
        "columns": ["name", "activity", "queue", "priority", "status", "assigned_resource", "task_count", "confirmed_count",
                    "wave", "batch_key", "reference_doctype", "reference_name", "started_at", "completed_at", "blocking_reason", "modified"],
        "virtual": {},
    },
    "hu": {
        "doctype": "Handling Unit", "title": "Handling Units",
        "selection": ["hu_number", "status", "hu_type", "current_bin", "storage_type", "outbound_delivery", "contains_product", "modified"],
        "columns": ["name", "hu_number", "hu_type", "current_bin", "parent_hu", "top_hu", "status", "stock_status", "outbound_delivery",
                    "shipment", "closed", "loaded", "gross_weight", "net_weight", "seal_number", "external_reference", "modified"],
        "virtual": {
            "storage_type": _bin_virtual("`tabHandling Unit`.current_bin"),
            "contains_product": {"label": "Contains Product", "fieldtype": "Link", "options": "Item", "column": "`sel_bal`.`product`",
                                 "sql": "`tabHandling Unit`.name in (select `sel_bal`.handling_unit from `tabWMS Stock Balance` `sel_bal` where `sel_bal`.quantity > 0 and {cond})"},
            "work_center": {"label": "Work Center", "fieldtype": "Link", "options": "Work Center", "column": "`sel_wc`.`name`",
                            "sql": "`tabHandling Unit`.current_bin in (select `sel_wc`.bin from `tabWork Center` `sel_wc` where {cond})"},
        },
    },
    "movements": {
        "doctype": "WMS Stock Ledger Entry", "title": "Stock Movements", "order_by": "`tabWMS Stock Ledger Entry`.posting_datetime desc",
        "selection": ["posting_datetime", "product", "storage_bin", "handling_unit", "movement_type", "reference_name", "posting_user", "batch_no"],
        "columns": ["name", "posting_datetime", "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type",
                    "quantity", "stock_uom", "movement_type", "reference_doctype", "reference_name", "warehouse_task", "posting_user"],
        "virtual": {"storage_type": _bin_virtual("`tabWMS Stock Ledger Entry`.storage_bin"), **_item_virtuals("`tabWMS Stock Ledger Entry`.product")},
    },
}


def _view(view):
    spec = VIEWS.get(view)
    if not spec:
        frappe.throw(_("Unknown monitor view {0}").format(view))
    return spec


@frappe.whitelist()
def get_selection_screen(view):
    """Everything the client needs to draw a view's selection screen: every selectable field,
    the default selection fields and columns, and this user's variants and layouts."""
    require_wms_access()
    spec = _view(view)
    frappe.has_permission(spec["doctype"], "read", throw=True)
    return {
        "view": view, "doctype": spec["doctype"], "title": _(spec["title"]),
        "fields": [{**f, "label": _(spec["labels"][f["fieldname"]])} if f["fieldname"] in spec.get("labels", {}) else f
                   for f in selectable_fields(spec["doctype"], spec.get("virtual"))],
        "selection": spec["selection"], "columns": spec["columns"],
        "default_criteria": spec.get("default_criteria") or {},
        "variants": list_variants(view),
    }


def _valid_columns(spec, columns):
    allowed = {f["fieldname"] for f in selectable_fields(spec["doctype"]) if not f.get("virtual")}
    cols = [c for c in (columns or spec["columns"]) if c in allowed]
    cols += [c for c in spec.get("required", []) if c not in cols]
    if "name" not in cols:
        cols.insert(0, "name")
    return cols


@frappe.whitelist()
@retry_on_deadlock
def execute_selection(view, warehouse, criteria=None, columns=None, max_hits=500, start=0):
    require_wms_access()
    spec = _view(view)
    frappe.get_doc("WMS Warehouse", warehouse).check_permission("read")
    if isinstance(columns, str):
        columns = json.loads(columns or "[]")
    fields = _valid_columns(spec, columns)
    rows = run_selection(spec["doctype"], criteria or {}, fields, base_filters={"warehouse": warehouse},
                         virtual=spec.get("virtual"), order_by=spec.get("order_by"), max_hits=max_hits, start=start)
    if view == "stock":
        _enrich_stock(rows)
    return {"rows": rows, "start": cint(start), "max_hits": cint(max_hits) or 500, "truncated": len(rows) >= (cint(max_hits) or 500)}


ACTIVE_ALLOCATION = ("Allocated", "Partially Picked")


def _enrich_stock(rows):
    """Adds what a balance row cannot carry itself, for the Stock Overview drill-down: the bin's
    storage type, its HU's parent and top HU, and the open allocations on it (`allocs`: outbound
    delivery, sales order and the quantity still reserved)."""
    def lookup(doctype, field_names, key_field, values):
        values = list({v for v in values if v})
        return {r[key_field]: r for r in frappe.get_all(doctype, filters={"name": ["in", values]}, fields=["name"] + field_names)} if values else {}
    bins = lookup("Storage Bin", ["storage_type"], "name", (r.get("storage_bin") for r in rows))
    hus = lookup("Handling Unit", ["parent_hu", "top_hu"], "name", (r.get("handling_unit") for r in rows))
    allocs = {}
    if rows:
        found = frappe.get_all("Stock Allocation", filters={"stock_balance": ["in", [r["name"] for r in rows]], "status": ["in", ACTIVE_ALLOCATION]},
                               fields=["stock_balance", "outbound_delivery", "outbound_delivery_item", "allocated_quantity", "picked_quantity"])
        orders = lookup("Outbound Delivery Item", ["sales_order"], "name", (a.outbound_delivery_item for a in found))
        for a in found:
            qty = (a.allocated_quantity or 0) - (a.picked_quantity or 0)
            if qty > 0:
                allocs.setdefault(a.stock_balance, []).append({"delivery": a.outbound_delivery, "sales_order": (orders.get(a.outbound_delivery_item) or {}).get("sales_order") or "", "qty": qty})
    for r in rows:
        r["storage_type"] = (bins.get(r.get("storage_bin")) or {}).get("storage_type") or ""
        hu = hus.get(r.get("handling_unit")) or {}
        r["parent_hu"], r["top_hu"] = hu.get("parent_hu") or "", hu.get("top_hu") or ""
        r["allocs"] = allocs.get(r["name"], [])


# ---- variants and layouts ----

VARIANT = "WMS Monitor Variant"


def _is_admin():
    roles = set(frappe.get_roles())
    return bool(roles & {"System Manager", "WMS Administrator", "WMS Supervisor", "WMS Process Engineer"})


@frappe.whitelist()
def list_variants(view):
    require_wms_access()
    user = frappe.session.user
    rows = frappe.get_all(VARIANT, filters={"view": view}, or_filters={"owner": user, "is_global": 1},
                          fields=["name", "variant_name", "variant_type", "description", "is_global", "is_default", "owner",
                                  "criteria", "layout", "selection_fields", "max_hits"],
                          order_by="variant_name asc")
    for r in rows:
        r["mine"] = r.owner == user
        # A global variant's "default" flag is its owner's; nobody else inherits it.
        if not r["mine"]:
            r["is_default"] = 0
        for k in ("criteria", "layout", "selection_fields"):
            r[k] = json.loads(r[k]) if r[k] else None
    return rows


@frappe.whitelist()
def save_variant(view, variant_type, variant_name, criteria=None, layout=None, selection_fields=None, max_hits=None,
                 description=None, is_default=0, is_global=0):
    require_wms_access()
    _view(view)
    if variant_type not in ("Selection", "Layout"):
        frappe.throw(_("Variant type must be Selection or Layout"))
    variant_name = (variant_name or "").strip()
    if not variant_name:
        frappe.throw(_("Give the variant a name"))
    if cint(is_global) and not _is_admin():
        frappe.throw(_("Only a WMS Supervisor or Process Engineer can save a global variant"), frappe.PermissionError)
    user = frappe.session.user
    dump = lambda v: json.dumps(json.loads(v) if isinstance(v, str) else v) if v not in (None, "") else None  # noqa: E731
    existing = frappe.db.get_value(VARIANT, {"view": view, "variant_type": variant_type, "variant_name": variant_name, "owner": user})
    doc = frappe.get_doc(VARIANT, existing) if existing else frappe.new_doc(VARIANT)
    doc.update({"view": view, "variant_type": variant_type, "variant_name": variant_name, "description": description,
                "criteria": dump(criteria), "layout": dump(layout), "selection_fields": dump(selection_fields),
                "max_hits": cint(max_hits) or None, "is_global": cint(is_global), "is_default": cint(is_default)})
    doc.save(ignore_permissions=True) if existing else doc.insert(ignore_permissions=True)
    if cint(is_default):
        frappe.db.sql(f"""update `tab{VARIANT}` set is_default=0 where owner=%s and view=%s and variant_type=%s and name<>%s""",
                      (user, view, variant_type, doc.name))
    return doc.name


@frappe.whitelist()
def set_default_variant(view, variant_type, variant=None):
    """Makes `variant` this user's default for the view (or clears the default when empty)."""
    require_wms_access()
    user = frappe.session.user
    frappe.db.sql(f"update `tab{VARIANT}` set is_default=0 where owner=%s and view=%s and variant_type=%s", (user, view, variant_type))
    if variant:
        owner = frappe.db.get_value(VARIANT, variant, "owner")
        if owner != user:
            frappe.throw(_("You can only make your own variants the default. Save a copy of it first."))
        frappe.db.set_value(VARIANT, variant, "is_default", 1)


@frappe.whitelist()
def delete_variant(variant):
    require_wms_access()
    owner = frappe.db.get_value(VARIANT, variant, "owner")
    if not owner:
        return
    if owner != frappe.session.user and not ("System Manager" in frappe.get_roles()):
        frappe.throw(_("You can only delete your own variants"), frappe.PermissionError)
    frappe.delete_doc(VARIANT, variant, ignore_permissions=True)
