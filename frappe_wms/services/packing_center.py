"""Packing Center (WMS Monitor): the bin > HU > nested HU > product hierarchy as one tree, repacking
by moving whole nodes, and creating Handling Units the proper way (packing material, bin, count)."""
import frappe
from frappe import _
from frappe.utils import cint, flt

from frappe_wms.services.handling_unit import (
    create_handling_unit, nest_handling_unit, relocate_handling_unit, unnest_handling_unit,
)
from frappe_wms.services.idempotency import run_once
from frappe_wms.services.numbering import next_number
from frappe_wms.services.packing import repack_loose
from frappe_wms.services.packing_station import hu_subtree
from frappe_wms.services.task import create_and_confirm_move

MAX_BINS = 600
MAX_BALANCE_ROWS = 30000
MAX_CREATE = 200

BIN_FIELDS = ["name", "bin_name", "storage_type", "storage_section", "bin_type", "maximum_hus", "current_hu_count",
              "putaway_blocked", "removal_blocked", "inventory_blocked", "active", "owner", "creation", "modified"]
HU_FIELDS = ["name", "hu_number", "hu_type", "packaging_material", "current_bin", "parent_hu", "top_hu", "hierarchy_level",
             "status", "stock_status", "gross_weight", "net_weight", "tare_weight", "volume", "outbound_delivery", "shipment",
             "seal_number", "external_reference", "sscc", "closed", "loaded", "owner", "creation", "modified"]
BALANCE_FIELDS = ["name", "product", "batch_no", "serial_no", "handling_unit", "storage_bin", "stock_type", "quantity",
                  "allocated_quantity", "available_quantity", "stock_uom", "first_receipt_date", "shelf_life_expiry_date", "last_movement_date"]


def _join(values, limit=3):
    vals = sorted({v for v in values if v})
    return ", ".join(vals) if len(vals) <= limit else _("{0} values").format(len(vals))


def packing_tree(warehouse, bins):
    """The whole forest for the given bins as one DFS-ordered list of rows (bin, HU, product), each
    row carrying every column the Packing Center can show. Product rows group the balance lines of
    one container by product / batch / stock type; their serial numbers stay in `lines`."""
    from frappe_wms.api.selection import _enrich_stock  # allocations (document / sales order) per balance row
    bins = list(dict.fromkeys(b for b in bins if b))[:MAX_BINS]
    if not bins:
        return {"rows": [], "truncated": False}
    bin_docs = {b.name: b for b in frappe.get_all("Storage Bin", filters={"warehouse": warehouse, "name": ["in", bins]}, fields=BIN_FIELDS)}
    bins = [b for b in bins if b in bin_docs]
    hus = frappe.get_all("Handling Unit", filters={"current_bin": ["in", bins]}, fields=HU_FIELDS, order_by="hu_number asc")
    balances = frappe.get_all("WMS Stock Balance", filters={"storage_bin": ["in", bins], "quantity": [">", 0]}, fields=BALANCE_FIELDS,
                              order_by="product asc", limit=MAX_BALANCE_ROWS + 1)
    truncated = len(balances) > MAX_BALANCE_ROWS
    balances = balances[:MAX_BALANCE_ROWS]
    _enrich_stock(balances)
    items = {i.name: i for i in frappe.get_all("Item", filters={"name": ["in", list({b.product for b in balances})]}, fields=["name", "item_name", "item_group"])} if balances else {}

    hu_by_name = {h.name: h for h in hus}
    kids = {}  # container id -> child HU rows
    for h in hus:
        parent = h.parent_hu if h.parent_hu in hu_by_name else None
        kids.setdefault(f"hu:{parent}" if parent else f"bin:{h.current_bin}", []).append(h)
    products = {}  # container id -> {(product, batch, stock type): [balance rows]}
    for b in balances:
        container = f"hu:{b.handling_unit}" if b.handling_unit in hu_by_name else f"bin:{b.storage_bin}"
        products.setdefault(container, {}).setdefault((b.product, b.batch_no or "", b.stock_type), []).append(b)

    out = []

    def add(row, container):
        row["has_kids"] = bool(kids.get(container) or products.get(container))
        out.append(row)

    def walk(container, depth, pid, ctx):
        for h in kids.get(container, []):
            cid = f"hu:{h.name}"
            add({"id": cid, "pid": pid, "kind": "hu", "depth": depth, "name": h.hu_number, "handling_unit": h.name,
                 "storage_bin": h.current_bin, "storage_type": ctx["storage_type"], "hu_status": h.status,
                 **{k: h.get(k) for k in HU_FIELDS if k not in ("name", "status", "current_bin")}}, cid)
            walk(cid, depth + 1, cid, ctx)
        for (product, batch, stock_type), lines in sorted(products.get(container, {}).items()):
            first = lines[0]
            item = items.get(product) or {}
            allocs = [a for ln in lines for a in ln.allocs]
            out.append({
                "id": f"p:{container}:{product}:{batch}:{stock_type}", "pid": pid, "kind": "product", "depth": depth, "has_kids": False,
                "name": product, "product": product, "product_name": item.get("item_name"), "product_group": item.get("item_group"),
                "batch_no": batch, "stock_type": stock_type, "stock_uom": first.stock_uom, "storage_bin": first.storage_bin,
                "storage_type": ctx["storage_type"], "handling_unit": first.handling_unit,
                "quantity": sum(flt(x.quantity) for x in lines), "allocated_quantity": sum(flt(x.allocated_quantity) for x in lines),
                "available_quantity": sum(flt(x.available_quantity) for x in lines),
                "serial_count": len({x.serial_no for x in lines if x.serial_no}),
                "first_receipt_date": min((str(x.first_receipt_date) for x in lines if x.first_receipt_date), default=""),
                "shelf_life_expiry_date": min((str(x.shelf_life_expiry_date) for x in lines if x.shelf_life_expiry_date), default=""),
                "last_movement_date": max((str(x.last_movement_date) for x in lines if x.last_movement_date), default=""),
                "document": _join(a["delivery"] for a in allocs), "sales_order": _join(a["sales_order"] for a in allocs),
                "lines": [{"serial_no": x.serial_no, "batch_no": x.batch_no, "quantity": x.quantity, "stock_uom": x.stock_uom, "stock_type": x.stock_type,
                           "product": x.product, "source_bin": x.storage_bin, "source_hu": x.handling_unit or None,
                           "first_receipt_date": str(x.first_receipt_date or "")} for x in lines],
            })

    for bin_name in bins:
        b = bin_docs[bin_name]
        cid = f"bin:{bin_name}"
        add({"id": cid, "pid": None, "kind": "bin", "depth": 0, "name": bin_name, "storage_bin": bin_name,
             **{k: b.get(k) for k in BIN_FIELDS if k not in ("name", "storage_type")}, "storage_type": b.storage_type}, cid)
        walk(cid, 1, cid, {"storage_type": b.storage_type})
    return {"rows": out, "truncated": truncated}


def _fail_text(e):
    frappe.clear_messages()
    return str(e) or e.__class__.__name__


def move_nodes(warehouse, items, destination_kind, destination, idempotency_key):
    """Drop `items` on a bin or an HU. An item is {"kind": "hu", "name"} (nested HU, or relocated
    when dropped on a bin) or {"kind": "stock", "label", "lines": [...]} (loose repack inside one
    bin, a real move between bins). Every item runs in its own savepoint so one refusal doesn't
    undo the rest; the answer says how many went through and why the others did not."""
    if destination_kind not in ("hu", "bin"):
        frappe.throw(_("Drop on a Handling Unit or a Storage Bin"))
    dest_hu = destination if destination_kind == "hu" else None
    dest_bin = frappe.db.get_value("Handling Unit", destination, "current_bin") if dest_hu else destination
    if not dest_bin or frappe.db.get_value("Storage Bin", dest_bin, "warehouse") != warehouse:
        frappe.throw(_("Destination {0} is not in warehouse {1}").format(destination, warehouse))
    moved, errors = 0, []
    for n, item in enumerate(items, 1):
        label = item.get("label") or item.get("name") or str(n)
        savepoint = f"pc_move_{n}"
        frappe.db.savepoint(savepoint)
        try:
            if item["kind"] == "hu":
                _move_hu(item["name"], dest_hu, dest_bin)
            else:
                _move_stock(warehouse, item["lines"], dest_hu, dest_bin, f"{idempotency_key}:{n}")
            moved += 1
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            frappe.db.rollback(save_point=savepoint)
            errors.append({"item": label, "error": _fail_text(e)})
    return {"moved": moved, "errors": errors}


def _move_hu(hu_name, dest_hu, dest_bin):
    if dest_hu:
        if dest_hu in hu_subtree(hu_name):
            frappe.throw(_("{0} cannot be packed into itself or into something nested inside it").format(hu_name))
        if frappe.db.get_value("Handling Unit", hu_name, "parent_hu") == dest_hu:
            frappe.throw(_("{0} is already inside {1}").format(hu_name, dest_hu))
        if frappe.db.get_value("Handling Unit", hu_name, "parent_hu"):
            unnest_handling_unit(hu_name)
        nest_handling_unit(hu_name, dest_hu)
    else:
        relocate_handling_unit(hu_name, dest_bin)


def _move_stock(warehouse, lines, dest_hu, dest_bin, key):
    by_source = {}
    for ln in lines:
        by_source.setdefault((ln["source_bin"], ln.get("source_hu") or None), []).append(ln)
    for (src_bin, src_hu), group in by_source.items():
        if src_bin == dest_bin and src_hu == dest_hu:
            frappe.throw(_("Already in {0}").format(dest_hu or dest_bin))
        if src_bin == dest_bin:
            repack_loose(dest_bin, src_hu, dest_hu, [
                {"item": ln["product"], "batch_no": ln.get("batch_no") or None, "serial_no": ln.get("serial_no") or None,
                 "stock_type": ln["stock_type"], "stock_uom": ln["stock_uom"], "quantity": flt(ln["quantity"])} for ln in group],
                "Handling Unit", dest_hu or src_hu or dest_bin, f"{key}:{src_bin}:{src_hu}")
        else:
            for i, ln in enumerate(group, 1):
                run_once(f"{key}:{src_bin}:{src_hu}:{i}", lambda ln=ln: create_and_confirm_move(
                    warehouse=warehouse, product=ln["product"], quantity=flt(ln["quantity"]), stock_uom=ln["stock_uom"], stock_type=ln["stock_type"],
                    source_bin=src_bin, source_hu=src_hu, destination_bin=dest_bin, destination_hu=dest_hu,
                    batch_no=ln.get("batch_no") or None, serial_no=ln.get("serial_no") or None))


def create_hus(warehouse, storage_bin, packaging_material=None, hu_type=None, hu_number=None, quantity=1, parent_hu=None):
    """Creates `quantity` Handling Units in `storage_bin` (an HU is always somewhere). The HU type
    comes from the packing material (or is given directly); an empty number is generated from the
    number range, a typed/scanned one only works for a single HU."""
    quantity = cint(quantity) or 1
    hu_number = (hu_number or "").strip() or None
    if not 1 <= quantity <= MAX_CREATE:
        frappe.throw(_("Create between 1 and {0} Handling Units at a time").format(MAX_CREATE))
    if hu_number and quantity != 1:
        frappe.throw(_("A given HU number can only be used for one Handling Unit - leave it empty to create several"))
    if not storage_bin and not parent_hu:
        frappe.throw(_("A Handling Unit has to be created in a Storage Bin"))
    if parent_hu:
        storage_bin = frappe.db.get_value("Handling Unit", parent_hu, "current_bin")
    if frappe.db.get_value("Storage Bin", storage_bin, "warehouse") != warehouse:
        frappe.throw(_("Storage Bin {0} is not in warehouse {1}").format(storage_bin, warehouse))
    if packaging_material and not hu_type:
        hu_type = frappe.db.get_value("Packaging Material", packaging_material, "hu_type")
    if not hu_type:
        frappe.throw(_("Choose a packing material (or an HU type)"))
    generate = not hu_number and frappe.db.get_value("Handling Unit Type", hu_type, "numbering_mode") != "Internal"
    created = []
    for _i in range(quantity):
        number = next_number("Handling Unit", warehouse=warehouse, hu_type=hu_type) if generate else hu_number
        hu = create_handling_unit(number, hu_type, storage_bin, parent_hu, warehouse, packaging_material)
        created.append({"name": hu["name"], "hu_number": hu["hu_number"], "hu_type": hu["hu_type"], "current_bin": hu["current_bin"]})
    return created
