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


def _date_time(value):
    text = str(value or "")
    return (text[:10], text[11:19]) if len(text) > 10 else (text, "")


def _join(values, limit=3):
    vals = sorted({v for v in values if v})
    return ", ".join(vals) if len(vals) <= limit else _("{0} values").format(len(vals))


def packing_tree(warehouse, bins, hus=None, balances=None, extra_hus=None):
    """The forest for the given bins as one DFS-ordered list of rows (section, bin, HU, product), each
    row carrying every column the Packing Center can show. Product rows group the balance lines of
    one container by product / batch / stock type; their serial numbers stay in `lines`.

    What is shown follows what was searched, so the tree stays clear: a search by bin shows
    everything in the bin; a search by HU (`hus`) shows only those HUs with what is inside them; a
    search by product / batch / serial (`balances`) shows only those stock lines and the HUs holding
    them. `extra_hus` are HUs created or used in the Packing Center since the search - always shown.
    """
    from frappe_wms.api.selection import _enrich_stock  # allocations (document / sales order) per balance row
    bins = list(dict.fromkeys(b for b in bins if b))[:MAX_BINS]
    if not bins:
        return {"rows": [], "truncated": False}
    bin_docs = {b.name: b for b in frappe.get_all("Storage Bin", filters={"warehouse": warehouse, "name": ["in", bins]}, fields=BIN_FIELDS)}
    bins = [b for b in bins if b in bin_docs]
    all_hus = frappe.get_all("Handling Unit", filters={"current_bin": ["in", bins]}, fields=HU_FIELDS, order_by="hu_number asc")
    stock = frappe.get_all("WMS Stock Balance", filters={"storage_bin": ["in", bins], "quantity": [">", 0]}, fields=BALANCE_FIELDS,
                           order_by="product asc", limit=MAX_BALANCE_ROWS + 1)
    truncated = len(stock) > MAX_BALANCE_ROWS
    stock = stock[:MAX_BALANCE_ROWS]
    extra = set(extra_hus or [])
    if hus is None and balances is None:
        hus = all_hus  # a bin search: everything in the bin
        loose_ok = True
    else:
        wanted = set(extra)
        if hus is not None:
            wanted |= set(hus)
        if balances is not None:
            wanted_lines = set(balances)
            stock = [b for b in stock if b.name in wanted_lines or b.handling_unit in extra]
            if hus is None:
                wanted |= {b.handling_unit for b in stock if b.handling_unit}
        # whatever is nested inside a wanted HU belongs to it
        grew = True
        while grew:
            grew = False
            for h in all_hus:
                if h.name not in wanted and h.parent_hu in wanted:
                    wanted.add(h.name)
                    grew = True
        hus = [h for h in all_hus if h.name in wanted]
        loose_ok = balances is not None
    shown = {h.name for h in hus}
    balances = [b for b in stock if (b.handling_unit in shown) or (not b.handling_unit and loose_ok)]
    _enrich_stock(balances)
    names = list({b.product for b in balances})
    items = {i.name: i for i in frappe.get_all("Item", filters={"name": ["in", names]}, fields=["name", "item_name", "item_group", "country_of_origin"])} if names else {}
    serial_control = {p.item: p.serial_control for p in frappe.get_all("WMS Product", filters={"item": ["in", names]}, fields=["item", "serial_control"])} if names else {}
    stock_types = {t.name: t.stock_type_name for t in frappe.get_all("WMS Stock Type", fields=["name", "stock_type_name"])}
    hu_categories = {t.name: t.category for t in frappe.get_all("Handling Unit Type", fields=["name", "category"])}

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
                 "storage_bin": h.current_bin, "storage_type": ctx["storage_type"], "storage_section": ctx["storage_section"], "hu_status": h.status,
                 "hu_category": hu_categories.get(h.hu_type), "creation_date": _date_time(h.creation)[0], "creation_time": _date_time(h.creation)[1],
                 **{k: h.get(k) for k in HU_FIELDS if k not in ("name", "status", "current_bin")}}, cid)
            walk(cid, depth + 1, cid, ctx)
        for (product, batch, stock_type), lines in sorted(products.get(container, {}).items()):
            first = lines[0]
            item = items.get(product) or {}
            allocs = [a for ln in lines for a in ln.allocs]
            out.append({
                "id": f"p:{container}:{product}:{batch}:{stock_type}", "pid": pid, "kind": "product", "depth": depth, "has_kids": False,
                "name": product, "product": product, "product_name": item.get("item_name"), "product_group": item.get("item_group"),
                "country_of_origin": item.get("country_of_origin"), "serial_control": serial_control.get(product) or "",
                "batch_no": batch, "stock_type": stock_type, "stock_type_name": stock_types.get(stock_type), "stock_uom": first.stock_uom, "storage_bin": first.storage_bin,
                "storage_type": ctx["storage_type"], "storage_section": ctx["storage_section"], "handling_unit": first.handling_unit,
                "gr_date": _date_time(min((str(x.first_receipt_date) for x in lines if x.first_receipt_date), default=""))[0],
                "gr_time": _date_time(min((str(x.first_receipt_date) for x in lines if x.first_receipt_date), default=""))[1],
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

    # Section > bin > HU > product (SAP's "Section/Bin/HU/Item"); a bin without a section is listed apart.
    by_section = {}
    for bin_name in bins:
        by_section.setdefault(bin_docs[bin_name].storage_section or "", []).append(bin_name)
    for section in sorted(by_section, key=lambda x: (x == "", x)):
        sid = f"sec:{section}"
        out.append({"id": sid, "pid": None, "kind": "section", "depth": 0, "has_kids": True, "name": section or _("(no section)"), "storage_section": section})
        for bin_name in by_section[section]:
            b = bin_docs[bin_name]
            cid = f"bin:{bin_name}"
            add({"id": cid, "pid": sid, "kind": "bin", "depth": 1, "name": bin_name, "storage_bin": bin_name,
                 "creation_date": _date_time(b.creation)[0], "creation_time": _date_time(b.creation)[1],
                 **{k: b.get(k) for k in BIN_FIELDS if k not in ("name", "storage_type")}, "storage_type": b.storage_type}, cid)
            walk(cid, 2, cid, {"storage_type": b.storage_type, "storage_section": b.storage_section})
    # how much is inside each node: product lines and handling units (nested ones included)
    index = {r["id"]: r for r in out}
    for r in out:
        r["product_items"] = 1 if r["kind"] == "product" else 0
        r["hu_count"] = 0
    for r in reversed(out):
        parent = index.get(r["pid"])
        if parent:
            parent["product_items"] += r["product_items"]
            parent["hu_count"] += r["hu_count"] + (1 if r["kind"] == "hu" else 0)
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
            from frappe_wms.services.locks import require_free_many
            require_free_many([("Handling Unit", h) for h in {item.get("name") if item["kind"] == "hu" else None, dest_hu, *[l.get("handling_unit") for l in item.get("lines", [])]} if h])
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
        frappe.throw(_("Choose the HU type"))
    generate = not hu_number and frappe.db.get_value("Handling Unit Type", hu_type, "numbering_mode") != "Internal"
    created = []
    for _i in range(quantity):
        number = next_number("Handling Unit", warehouse=warehouse, hu_type=hu_type) if generate else hu_number
        hu = create_handling_unit(number, hu_type, storage_bin, parent_hu, warehouse, packaging_material)
        created.append({"name": hu["name"], "hu_number": hu["hu_number"], "hu_type": hu["hu_type"], "current_bin": hu["current_bin"]})
    return created


def post_differences(warehouse, items, remarks, idempotency_key):
    """The marked product lines are short of what the system shows: the missing quantity of each
    goes to the warehouse's difference bin, with a WMS Task Difference for the Difference Analyzer
    (same booking the packing station makes, without needing a work center). Stock picked for a
    delivery is not handled here - complete that delivery short or reverse the pick."""
    from frappe_wms.utils import require_role
    require_role("WMS Supervisor", "WMS Inventory Controller", "WMS Packer")  # writes stock off: not for the read-only roles
    posted, errors = 0, []
    for n, item in enumerate(items, 1):
        savepoint = f"pc_diff_{n}"
        frappe.db.savepoint(savepoint)
        try:
            for i, ln in enumerate(item["lines"], 1):
                _post_difference(warehouse, ln, remarks, f"{idempotency_key}:{n}:{i}")
            posted += 1
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as e:
            frappe.db.rollback(save_point=savepoint)
            errors.append({"item": item.get("label") or str(n), "error": _fail_text(e)})
    return {"posted": posted, "errors": errors}


def _post_difference(warehouse, ln, remarks, key):
    from frappe_wms.services.difference import difference_bin_for_warehouse
    from frappe_wms.services.packing_station import hu_deliveries
    from frappe_wms.services.stock import transfer_stock
    from frappe.utils import now_datetime
    quantity = flt(ln["quantity"])
    if quantity <= 0:
        frappe.throw(_("Enter the missing quantity"))
    source_hu = ln.get("source_hu") or None
    if source_hu and hu_deliveries(source_hu):
        frappe.throw(_("{0} holds stock picked for {1}. Complete the delivery short or reverse the pick instead of posting a difference here.")
                     .format(source_hu, ", ".join(sorted(hu_deliveries(source_hu)))))
    diff_bin = difference_bin_for_warehouse(warehouse)
    src = {"warehouse": warehouse, "product": ln["product"], "batch_no": ln.get("batch_no") or None, "serial_no": ln.get("serial_no") or None,
           "handling_unit": source_hu, "storage_bin": ln["source_bin"], "stock_type": ln["stock_type"], "stock_uom": ln["stock_uom"]}
    task = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Repack", "warehouse": warehouse, "product": ln["product"], "planned_quantity": quantity,
                           "stock_uom": ln["stock_uom"], "batch_no": src["batch_no"], "serial_no": src["serial_no"], "source_bin": ln["source_bin"],
                           "destination_bin": diff_bin, "source_hu": source_hu, "stock_type_from": ln["stock_type"], "stock_type_to": ln["stock_type"],
                           "movement_type": "301", "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
    transfer_stock(source=src, destination={"handling_unit": None, "storage_bin": diff_bin, "stock_type": ln["stock_type"]}, quantity=quantity,
                   movement_type="301", reference_doctype="Handling Unit" if source_hu else "Storage Bin", reference_name=source_hu or ln["source_bin"],
                   idempotency_key=f"PCDIFF:{key}", warehouse_task=task.name)
    task.db_set({"confirmed_quantity": quantity, "status": "Confirmed", "confirmed_at": now_datetime(), "confirmed_by": frappe.session.user, "docstatus": 1})
    frappe.get_doc({"doctype": "WMS Task Difference", "warehouse": warehouse, "warehouse_task": task.name, "task_type": "Repack", "product": ln["product"],
                    "stock_uom": ln["stock_uom"], "batch_no": src["batch_no"], "serial_no": src["serial_no"], "direction": "Short", "planned_quantity": quantity,
                    "difference_quantity": quantity, "stock_type": ln["stock_type"], "storage_bin": diff_bin,
                    "clearance_remarks": remarks or _("Missing, posted from the Packing Center"), "status": "Open"}).insert(ignore_permissions=True)
