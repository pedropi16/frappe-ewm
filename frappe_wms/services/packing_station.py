"""Packing work center (SAP EWM /SCWM/PACK): a packer logs on to a Work Center - a packing
table mapped to one Storage Bin - and packs, nests, creates, closes and reopens Handling Units
there, mostly by scanning.

Delivery awareness: an HU on the table "belongs" to the outbound deliveries whose stock it holds
(explicitly through Handling Unit.outbound_delivery, or implicitly through the Pick / Cross Dock
tasks that staged stock into it). Packing never mixes two deliveries in one HU, and packing into
an empty HU stamps it with the delivery, which is how shipping and goods issue later find stock
that was repacked out of the pick HU into a shipping carton (services/shipping.py, issue.py).
"""
import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from frappe_wms.services.handling_unit import (
    create_handling_unit, nest_handling_unit, relocate_handling_unit, unnest_handling_unit,
)
from frappe_wms.services.packing import PACKING_ROLES, repack_loose
from frappe_wms.services.printing import create_print_spool
from frappe_wms.utils import require_role

EPS = 0.000001


# ---------------------------------------------------------------- HU helpers

def hu_subtree(hu_name):
    """The HU itself plus every HU nested in it, at any depth."""
    out, stack = [], [hu_name]
    while stack:
        name = stack.pop()
        if name in out: continue
        out.append(name)
        stack.extend(frappe.get_all("Handling Unit", filters={"parent_hu": name}, pluck="name"))
    return out


def top_hu(hu_name):
    seen, cur = set(), hu_name
    while True:
        parent = frappe.db.get_value("Handling Unit", cur, "parent_hu")
        if not parent or parent in seen: return cur
        seen.add(cur)
        cur = parent


def subtree_quantity(hu_name):
    names = hu_subtree(hu_name)
    return flt(frappe.db.sql("select sum(quantity) from `tabWMS Stock Balance` where handling_unit in %s and quantity > 0",
                             (tuple(names),))[0][0])


def _open_pick_deliveries(hu_names):
    tasks = frappe.get_all("Warehouse Task", filters={"task_type": "Pick", "status": "Confirmed", "destination_hu": ["in", hu_names]}, pluck="name")
    if not tasks: return set()
    allocations = frappe.get_all("Warehouse Task Allocation", filters={"parent": ["in", tasks]}, pluck="stock_allocation")
    if not allocations: return set()
    rows = frappe.get_all("Stock Allocation", filters={"name": ["in", allocations]}, fields=["outbound_delivery", "outbound_delivery_item"])
    out = set()
    for r in rows:
        if not r.outbound_delivery: continue
        line = frappe.db.get_value("Outbound Delivery Item", r.outbound_delivery_item, ["picked_quantity", "issued_quantity"], as_dict=True)
        if line and flt(line.picked_quantity) - flt(line.issued_quantity) > EPS:
            out.add(r.outbound_delivery)
    return out


def _cross_dock_deliveries(hu_names):
    tasks = frappe.get_all("Warehouse Task", filters={"task_type": "Cross Dock", "status": "Confirmed", "destination_hu": ["in", hu_names]},
                           fields=["warehouse_request"])
    requests = [t.warehouse_request for t in tasks if t.warehouse_request]
    if not requests: return set()
    names = frappe.get_all("Warehouse Request", filters={"name": ["in", requests], "reference_doctype": "Outbound Delivery"}, pluck="reference_name")
    return {n for n in names if frappe.db.get_value("Outbound Delivery", n, "goods_issue_status") != "Posted"}


def hu_deliveries(hu_name):
    """Every open outbound delivery with stock in this HU (or anything nested in it)."""
    names = hu_subtree(hu_name)
    explicit = set(frappe.get_all("Handling Unit", filters={"name": ["in", names], "outbound_delivery": ["is", "set"]}, pluck="outbound_delivery"))
    explicit = {d for d in explicit if frappe.db.get_value("Outbound Delivery", d, "goods_issue_status") != "Posted"}
    return explicit | _open_pick_deliveries(names) | _cross_dock_deliveries(names)


# ---------------------------------------------------------------- station context

CONFIG_FIELDS = ["name", "warehouse", "bin", "active", "work_center_name", "work_center_code", "work_center_type",
                 "inbound_section_bin", "outbound_section_bin", "default_hu_type", "quantity_proposal", "weigh_on_close",
                 "weight_tolerance_percent", "completeness_check", "close_follow_up", "print_label_on_create", "print_label_on_close",
                 "allow_pack_product", "allow_pack_hu", "allow_unpack", "allow_pack_by_instruction", "allow_create_hu",
                 "allow_close_hu", "allow_delete_empty_hu", "allow_differences"]
FUNCTION_LABELS = {"allow_pack_product": "Pack Product", "allow_pack_hu": "Pack HU into HU", "allow_unpack": "Take HU out of HU",
                   "allow_pack_by_instruction": "Pack by Packing Instruction", "allow_create_hu": "Create HU",
                   "allow_close_hu": "Close / Reopen HU", "allow_delete_empty_hu": "Delete Empty HU",
                   "allow_differences": "Post Missing Quantity"}


def _station(work_center, function=None):
    require_role(*PACKING_ROLES)
    wc = frappe.db.get_value("Work Center", work_center, CONFIG_FIELDS, as_dict=True)
    if not wc: frappe.throw(_("Work Center {0} not found").format(work_center))
    if not wc.active: frappe.throw(_("Work Center {0} is not active").format(work_center))
    if function and not wc.get(function):
        frappe.throw(_("{0} is switched off for Work Center {1} (Work Center > Enabled Functions)").format(_(FUNCTION_LABELS[function]), wc.name),
                     title=_("Function Not Enabled"))
    return wc


def _hu_at_station(wc, hu_name, label=None):
    hu = frappe.db.get_value("Handling Unit", hu_name, ["name", "current_bin", "warehouse", "closed", "status", "outbound_delivery", "parent_hu"], as_dict=True)
    if not hu: frappe.throw(_("{0} {1} not found").format(label or _("Handling Unit"), hu_name))
    if hu.current_bin != wc.bin:
        frappe.throw(_("{0} {1} is in bin {2}, not at this packing station ({3})").format(label or _("Handling Unit"), hu_name, hu.current_bin or "-", wc.bin))
    return hu


def _require_open(hu):
    if hu.closed or hu.status == "Closed":
        frappe.throw(_("Handling Unit {0} is closed - reopen it before packing into it").format(hu.name))
    if hu.status in ("Loaded", "Shipped", "Blocked", "Cancelled"):
        frappe.throw(_("Handling Unit {0} is {1} and cannot be packed").format(hu.name, _(hu.status)))


def _resolve_delivery(source_deliveries, destination_hu, chosen):
    """Which delivery the destination HU will hold after this pack step. One HU never carries
    two deliveries: shipping and goods issue find a delivery's stock through its HUs."""
    source_deliveries = set(source_deliveries or ())
    if chosen and source_deliveries and chosen not in source_deliveries:
        frappe.throw(_("The stock being packed is for {0}, not {1}").format(", ".join(sorted(source_deliveries)), chosen))
    if len(source_deliveries) > 1 and not chosen:
        frappe.throw(_("The source holds stock for several deliveries ({0}) - choose which delivery this pack step is for")
                     .format(", ".join(sorted(source_deliveries))), title=_("Choose a delivery"))
    target = chosen or (next(iter(source_deliveries)) if source_deliveries else None)
    destination = hu_deliveries(destination_hu)
    if destination and target and destination != {target}:
        frappe.throw(_("Handling Unit {0} already holds stock for {1} - one HU cannot mix deliveries. Pack {2} into a different HU.")
                     .format(destination_hu, ", ".join(sorted(destination)), target))
    if destination and not target:
        frappe.throw(_("Handling Unit {0} holds stock for {1}; this stock is not assigned to that delivery")
                     .format(destination_hu, ", ".join(sorted(destination))))
    return target


def _stamp_delivery(hu_name, delivery):
    if delivery and not frappe.db.get_value("Handling Unit", hu_name, "outbound_delivery"):
        frappe.db.set_value("Handling Unit", hu_name, "outbound_delivery", delivery)
    if delivery:
        status = frappe.db.get_value("Outbound Delivery", delivery, "packing_status")
        if status in (None, "", "Not Started"):
            frappe.db.set_value("Outbound Delivery", delivery, "packing_status", "In Process")


def _event(hu_name, event_type, wc, **kw):
    frappe.get_doc({"doctype": "Handling Unit Event", "handling_unit": hu_name, "event_type": event_type, "bin_after": kw.pop("bin_after", wc.bin),
                    "reference_doctype": "Work Center", "reference_name": wc.name, "event_timestamp": now_datetime(),
                    "performed_by": frappe.session.user, **kw}).insert(ignore_permissions=True)


# ---------------------------------------------------------------- read side

def list_work_centers(warehouse=None):
    require_role(*PACKING_ROLES)
    filters = {"active": 1}
    if warehouse: filters["warehouse"] = warehouse
    return frappe.get_all("Work Center", filters=filters, fields=["name", "work_center_code", "work_center_name", "warehouse", "bin", "work_center_type"],
                          order_by="work_center_code asc")


def _node(hu_name):
    hu = frappe.db.get_value("Handling Unit", hu_name, ["name", "hu_number", "hu_type", "status", "stock_status", "closed", "gross_weight",
                                                        "net_weight", "tare_weight", "volume", "outbound_delivery", "parent_hu", "sscc"], as_dict=True)
    hu["stock"] = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu_name, "quantity": [">", 0]},
                                 fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"], order_by="product asc")
    hu["children"] = [_node(c) for c in frappe.get_all("Handling Unit", filters={"parent_hu": hu_name}, pluck="name", order_by="hu_number asc")]
    hu["deliveries"] = sorted(hu_deliveries(hu_name)) if hu.parent_hu is None else []
    return hu


def packing_instructions(product):
    """Packaging Spec levels for a product: [{level_name, quantity_per_level, hu_type}] - the SAP
    packing instruction proposal."""
    spec = frappe.db.get_value("Packaging Spec", {"item": product, "active": 1})
    if not spec: return []
    return frappe.get_all("Packaging Spec Level", filters={"parent": spec, "parenttype": "Packaging Spec"},
                          fields=["level_name", "quantity_per_level", "hu_type"], order_by="idx asc")


def station_overview(work_center):
    wc = _station(work_center)
    tops = frappe.get_all("Handling Unit", filters={"current_bin": wc.bin, "parent_hu": ["in", ["", None]],
                                                    "status": ["not in", ["Shipped", "Cancelled"]]}, pluck="name", order_by="modified desc")
    loose = frappe.get_all("WMS Stock Balance", filters={"storage_bin": wc.bin, "handling_unit": ["in", ["", None]], "quantity": [">", 0]},
                           fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"], order_by="product asc")
    arriving = frappe.get_all("Handling Unit", filters={"current_bin": wc.inbound_section_bin, "parent_hu": ["in", ["", None]],
                                                        "status": ["not in", ["Shipped", "Cancelled"]]},
                              fields=["name", "hu_number", "hu_type", "outbound_delivery"], order_by="modified asc") if wc.inbound_section_bin else []
    orders = frappe.get_all("Packing Order", filters={"work_center_bin": wc.bin, "status": ["in", ["Draft", "Open", "In Process"]]},
                            fields=["name", "outbound_delivery", "status"], order_by="creation asc")
    for o in orders:
        o["source_hus"] = frappe.get_all("Packing Source HU", filters={"parent": o.name}, pluck="handling_unit")
        o["destination_hus"] = frappe.get_all("Packing Destination HU", filters={"parent": o.name}, pluck="handling_unit")
    nodes = [_node(n) for n in tops]
    delivery_names = sorted({d for n in nodes for d in n["deliveries"]} | {o.outbound_delivery for o in orders if o.outbound_delivery})
    deliveries = {d.name: d for d in frappe.get_all("Outbound Delivery", filters={"name": ["in", delivery_names]},
                  fields=["name", "outbound_delivery_number", "customer", "route", "staging_bin", "door", "packing_status"])} if delivery_names else {}
    stock_uom = {s["product"]: s["stock_uom"] for n in nodes for s in _all_stock(n)} | {s.product: s.stock_uom for s in loose}
    products = sorted(stock_uom)
    from frappe_wms.services.uom import unit_options
    return {"units": {p: unit_options(p, stock_uom[p]) for p in products}, "work_center": wc, "handling_units": nodes, "loose_stock": loose, "arriving": arriving, "packing_orders": orders,
            "deliveries": deliveries, "instructions": {p: packing_instructions(p) for p in products},
            "hu_types": frappe.get_all("Handling Unit Type", filters={"active": 1}, fields=["name", "hu_type_name", "numbering_mode", "category"])}


def _all_stock(node):
    out = list(node.get("stock") or [])
    for c in node.get("children") or []: out += _all_stock(c)
    return out


# ---------------------------------------------------------------- write side

def _source_balance(wc, source_hu, product, stock_type=None, batch_no=None, serial_no=None):
    filters = {"storage_bin": wc.bin, "handling_unit": source_hu or ["in", ["", None]], "product": product, "quantity": [">", 0]}
    for k, v in (("stock_type", stock_type), ("batch_no", batch_no), ("serial_no", serial_no)):
        if v: filters[k] = v
    balances = frappe.get_all("WMS Stock Balance", filters=filters, fields=["product", "batch_no", "serial_no", "stock_type", "stock_uom", "quantity"])
    where = source_hu or _("loose on the table")
    if not balances: frappe.throw(_("No {0} in {1}").format(product, where))
    if len({(b.batch_no or "", b.serial_no or "", b.stock_type) for b in balances}) > 1:
        what = _("serial number") if any(b.serial_no for b in balances) else _("batch") if len({b.batch_no for b in balances}) > 1 else _("stock type")
        frappe.throw(_("{0} in {1} has more than one {2} - scan or choose which one").format(product, where, what))
    return balances[0]


def pack_product(work_center, product, quantity, destination_hu, source_hu=None, stock_type=None, batch_no=None, serial_no=None,
                 outbound_delivery=None, idempotency_key=None):
    """Moves `quantity` of one product from a source HU (or loose on the table) into the
    destination HU. The SAP "Pack Product" tab: scan source, scan product, quantity, scan target."""
    wc = _station(work_center, "allow_pack_product")
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Enter a quantity greater than zero"))
    if source_hu and source_hu == destination_hu: frappe.throw(_("Source and destination are the same Handling Unit"))
    dest = _hu_at_station(wc, destination_hu, _("Destination HU"))
    _require_open(dest)
    if source_hu:
        src = _hu_at_station(wc, source_hu, _("Source HU"))
        if src.closed: frappe.throw(_("Source HU {0} is closed - reopen it to take stock out").format(source_hu))
    bal = _source_balance(wc, source_hu, product, stock_type, batch_no, serial_no)
    if quantity - flt(bal.quantity) > EPS:
        frappe.throw(_("Only {0} {1} of {2} in {3}").format(flt(bal.quantity), bal.stock_uom, product, source_hu or _("loose on the table")))
    target = _resolve_delivery(hu_deliveries(source_hu) if source_hu else set(), destination_hu, outbound_delivery)
    frappe.get_doc("Handling Unit", destination_hu, for_update=True)
    repack_loose(wc.bin, source_hu, destination_hu, [{"item": product, "quantity": quantity, "stock_uom": bal.stock_uom,
                 "stock_type": bal.stock_type, "batch_no": bal.batch_no, "serial_no": bal.serial_no}],
                 "Work Center", wc.name, idempotency_key or f"PACK:{wc.name}:{frappe.generate_hash(length=12)}")
    _stamp_delivery(destination_hu, target)
    return {"destination_hu": destination_hu, "product": product, "quantity": quantity, "outbound_delivery": target}


def pack_all(work_center, source_hu, destination_hu, outbound_delivery=None, idempotency_key=None):
    """Everything in the source HU (not its nested HUs) into the destination - "Repack all"."""
    wc = _station(work_center, "allow_pack_product")
    if source_hu == destination_hu: frappe.throw(_("Source and destination are the same Handling Unit"))
    _hu_at_station(wc, source_hu, _("Source HU"))
    dest = _hu_at_station(wc, destination_hu, _("Destination HU"))
    _require_open(dest)
    target = _resolve_delivery(hu_deliveries(source_hu), destination_hu, outbound_delivery)
    items = [{"item": b.product, "quantity": b.quantity, "stock_uom": b.stock_uom, "stock_type": b.stock_type, "batch_no": b.batch_no, "serial_no": b.serial_no}
             for b in frappe.get_all("WMS Stock Balance", filters={"handling_unit": source_hu, "storage_bin": wc.bin, "quantity": [">", 0]},
                                     fields=["product", "quantity", "stock_uom", "stock_type", "batch_no", "serial_no"])]
    if not items: frappe.throw(_("{0} holds no stock of its own").format(source_hu))
    frappe.get_doc("Handling Unit", destination_hu, for_update=True)
    repack_loose(wc.bin, source_hu, destination_hu, items, "Work Center", wc.name, idempotency_key or f"PACKALL:{wc.name}:{frappe.generate_hash(length=12)}")
    _stamp_delivery(destination_hu, target)
    return {"destination_hu": destination_hu, "lines": len(items), "outbound_delivery": target}


def pack_by_instruction(work_center, product, source_hu=None, level_name=None, hu_type=None, quantity_per_hu=None,
                        stock_type=None, batch_no=None, serial_no=None, outbound_delivery=None, close=0, idempotency_key=None):
    """SAP "pack according to packing instruction": splits a product into new HUs of the
    Packaging Spec level's quantity and HU type (or a quantity/HU type given here). Leftover that
    does not fill a whole HU goes into one last partial HU."""
    wc = _station(work_center, "allow_pack_by_instruction")
    if not wc.allow_create_hu: _station(work_center, "allow_create_hu")
    levels = packing_instructions(product)
    level = next((l for l in levels if l.level_name == level_name), None) if level_name else (levels[0] if levels else None)
    per_hu = flt(quantity_per_hu) or (flt(level.quantity_per_level) if level else 0)
    hu_type = hu_type or (level.hu_type if level else None) or wc.default_hu_type
    if per_hu <= 0: frappe.throw(_("{0} has no Packaging Spec - enter the quantity per HU").format(product))
    if not hu_type: frappe.throw(_("No HU type: set one on the Packaging Spec level or the Work Center's Default Packaging"))
    if source_hu:
        _hu_at_station(wc, source_hu, _("Source HU"))
    if frappe.db.get_value("Handling Unit Type", hu_type, "numbering_mode") != "Internal":
        frappe.throw(_("HU type {0} is externally numbered - pack by instruction needs an internally numbered HU type").format(hu_type))
    bal = _source_balance(wc, source_hu, product, stock_type, batch_no, serial_no)
    sources = hu_deliveries(source_hu) if source_hu else set()
    if outbound_delivery and sources and outbound_delivery not in sources:
        frappe.throw(_("The stock being packed is for {0}, not {1}").format(", ".join(sorted(sources)), outbound_delivery))
    if len(sources) > 1 and not outbound_delivery:
        frappe.throw(_("The source holds stock for several deliveries ({0}) - choose which delivery to pack").format(", ".join(sorted(sources))))
    target = outbound_delivery or (next(iter(sources)) if sources else None)
    remaining, created, key = flt(bal.quantity), [], idempotency_key or f"PACKINS:{wc.name}:{frappe.generate_hash(length=10)}"
    while remaining > EPS:
        qty = min(per_hu, remaining)
        hu = create_handling_unit(None, hu_type, storage_bin=wc.bin, warehouse=wc.warehouse)
        repack_loose(wc.bin, source_hu, hu["name"], [{"item": product, "quantity": qty, "stock_uom": bal.stock_uom, "stock_type": bal.stock_type,
                     "batch_no": bal.batch_no, "serial_no": bal.serial_no}], "Work Center", wc.name, f"{key}:{len(created) + 1}")
        _stamp_delivery(hu["name"], target)
        created.append(hu["name"])
        remaining -= qty
    if int(close or 0):
        for name in created:
            close_hu(work_center, name, confirm_incomplete=1)
    return {"handling_units": created, "quantity_per_hu": per_hu, "hu_type": hu_type}


def pack_hu(work_center, hu_name, destination_hu, outbound_delivery=None):
    """Puts a whole HU (with everything inside it) into another HU - SAP "Repack HU"."""
    wc = _station(work_center, "allow_pack_hu")
    if hu_name == destination_hu: frappe.throw(_("A Handling Unit cannot be packed into itself"))
    hu = _hu_at_station(wc, hu_name)
    dest = _hu_at_station(wc, destination_hu, _("Destination HU"))
    _require_open(dest)
    if destination_hu in hu_subtree(hu_name): frappe.throw(_("{0} is inside {1} - that would pack an HU into itself").format(destination_hu, hu_name))
    target = _resolve_delivery(hu_deliveries(hu_name), destination_hu, outbound_delivery)
    if hu.parent_hu: unnest_handling_unit(hu_name)
    nest_handling_unit(hu_name, destination_hu)
    _stamp_delivery(destination_hu, target)
    return {"handling_unit": hu_name, "destination_hu": destination_hu, "outbound_delivery": target}


def unpack_hu(work_center, hu_name):
    """Takes a nested HU out of its parent onto the table."""
    wc = _station(work_center, "allow_unpack")
    hu = _hu_at_station(wc, hu_name)
    if not hu.parent_hu: frappe.throw(_("{0} is not packed inside another Handling Unit").format(hu_name))
    parent = frappe.db.get_value("Handling Unit", hu.parent_hu, ["name", "closed", "status"], as_dict=True)
    if parent.closed: frappe.throw(_("{0} is closed - reopen it first").format(parent.name))
    unnest_handling_unit(hu_name)
    return {"handling_unit": hu_name}


def take_to_table(work_center, hu_name):
    """Moves an HU waiting in the station's inbound section onto the packing table."""
    wc = _station(work_center)
    hu = frappe.db.get_value("Handling Unit", hu_name, ["name", "current_bin"], as_dict=True)
    if not hu: frappe.throw(_("Handling Unit {0} not found").format(hu_name))
    if hu.current_bin == wc.bin: return {"handling_unit": hu_name}
    if not wc.inbound_section_bin or hu.current_bin != wc.inbound_section_bin:
        frappe.throw(_("{0} is in {1}, not in this station's inbound section").format(hu_name, hu.current_bin or "-"))
    relocate_handling_unit(hu_name, wc.bin)
    return {"handling_unit": hu_name}


def create_station_hu(work_center, hu_type=None, hu_number=None, outbound_delivery=None):
    wc = _station(work_center, "allow_create_hu")
    hu_type = hu_type or wc.default_hu_type
    if not hu_type: frappe.throw(_("Choose the HU type (or set Default Packaging on the Work Center)"))
    frappe.flags.wms_skip_hu_created_print = not wc.print_label_on_create
    try:
        hu = create_handling_unit(hu_number or None, hu_type, storage_bin=wc.bin, warehouse=wc.warehouse)
    finally:
        frappe.flags.wms_skip_hu_created_print = False
    if outbound_delivery:
        frappe.db.set_value("Handling Unit", hu["name"], "outbound_delivery", outbound_delivery)
    return {"name": hu["name"], "hu_number": hu.get("hu_number"), "hu_type": hu_type}


def delete_empty_hu(work_center, hu_name):
    """SAP "Delete HU": an HU with nothing in it and nothing nested leaves the table."""
    wc = _station(work_center, "allow_delete_empty_hu")
    hu = _hu_at_station(wc, hu_name)
    if subtree_quantity(hu_name) > EPS or len(hu_subtree(hu_name)) > 1:
        frappe.throw(_("{0} still holds stock or other HUs").format(hu_name))
    if hu.parent_hu: unnest_handling_unit(hu_name)
    frappe.db.set_value("Handling Unit", hu_name, {"status": "Cancelled", "closed": 0, "current_bin": None, "outbound_delivery": None})
    _event(hu_name, "Cancelled", wc, bin_before=wc.bin, bin_after=None)
    return {"handling_unit": hu_name, "status": "Cancelled"}


def post_difference(work_center, product, quantity, source_hu=None, stock_type=None, batch_no=None, serial_no=None, remarks=None):
    """The table is short of what the system shows: the missing quantity goes to the warehouse
    difference bin (with a WMS Task Difference record for the Difference Analyzer). Stock picked
    for a delivery is not handled here - complete that delivery short or reverse the pick."""
    from frappe_wms.services.difference import difference_bin_for_warehouse
    from frappe_wms.services.stock import transfer_stock
    wc = _station(work_center, "allow_differences")
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Enter the missing quantity"))
    if source_hu:
        _hu_at_station(wc, source_hu, _("Source HU"))
        if hu_deliveries(source_hu):
            frappe.throw(_("{0} holds stock picked for {1}. Complete the delivery short or reverse the pick instead of posting a difference here.")
                         .format(source_hu, ", ".join(sorted(hu_deliveries(source_hu)))))
    bal = _source_balance(wc, source_hu, product, stock_type, batch_no, serial_no)
    if quantity - flt(bal.quantity) > EPS: frappe.throw(_("Only {0} {1} recorded in {2}").format(flt(bal.quantity), bal.stock_uom, source_hu or wc.bin))
    diff_bin = difference_bin_for_warehouse(wc.warehouse)
    task = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Repack", "warehouse": wc.warehouse, "product": product,
                           "planned_quantity": quantity, "stock_uom": bal.stock_uom, "batch_no": bal.batch_no, "serial_no": bal.serial_no,
                           "source_bin": wc.bin, "destination_bin": diff_bin, "source_hu": source_hu, "stock_type_from": bal.stock_type,
                           "stock_type_to": bal.stock_type, "movement_type": "301", "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
    transfer_stock(source={"warehouse": wc.warehouse, "product": product, "batch_no": bal.batch_no, "serial_no": bal.serial_no, "handling_unit": source_hu,
                           "storage_bin": wc.bin, "stock_type": bal.stock_type, "stock_uom": bal.stock_uom},
                   destination={"handling_unit": None, "storage_bin": diff_bin, "stock_type": bal.stock_type},
                   quantity=quantity, movement_type="301", reference_doctype="Work Center", reference_name=wc.name,
                   idempotency_key=f"PACKDIFF:{task.name}", warehouse_task=task.name)
    task.db_set({"confirmed_quantity": quantity, "status": "Confirmed", "confirmed_at": now_datetime(), "confirmed_by": frappe.session.user, "docstatus": 1})
    diff = frappe.get_doc({"doctype": "WMS Task Difference", "warehouse": wc.warehouse, "warehouse_task": task.name, "task_type": "Repack",
                           "product": product, "stock_uom": bal.stock_uom, "batch_no": bal.batch_no, "serial_no": bal.serial_no, "direction": "Short",
                           "planned_quantity": quantity, "difference_quantity": quantity, "stock_type": bal.stock_type, "storage_bin": diff_bin,
                           "clearance_remarks": remarks or _("Missing at packing station {0}").format(wc.name), "status": "Open"}).insert(ignore_permissions=True)
    return {"difference": diff.name, "moved_to": diff_bin}


def _unpacked_for_delivery(wc, delivery, except_hu):
    """Stock of this delivery still on the table outside closed HUs (completeness check)."""
    open_tops = [t for t in frappe.get_all("Handling Unit", filters={"current_bin": wc.bin, "parent_hu": ["in", ["", None]], "closed": 0,
                                                                    "status": ["not in", ["Shipped", "Cancelled"]]}, pluck="name") if t != except_hu]
    return [t for t in open_tops if delivery in hu_deliveries(t) and subtree_quantity(t) > EPS]


def close_hu(work_center, hu_name, gross_weight=None, move_to_bin=None, confirm_incomplete=0):
    """SAP "Close HU": weighs, checks, locks and labels the HU, then sends it on per the work
    center's Close HU Follow-up (or an explicit bin)."""
    wc = _station(work_center, "allow_close_hu")
    hu = _hu_at_station(wc, hu_name)
    if hu.closed: frappe.throw(_("{0} is already closed").format(hu_name))
    if hu.parent_hu: frappe.throw(_("{0} is packed inside {1} - close the outer HU instead").format(hu_name, hu.parent_hu))
    if subtree_quantity(hu_name) <= EPS: frappe.throw(_("{0} is empty - nothing to close").format(hu_name))
    values = {"closed": 1, "status": "Closed"}
    weighed = gross_weight not in (None, "") and flt(gross_weight) > 0
    if gross_weight not in (None, "") and flt(gross_weight) <= 0: frappe.throw(_("Gross weight must be greater than zero"))
    if wc.weigh_on_close == "Required" and not weighed:
        frappe.throw(_("Weigh {0} - this station requires the gross weight on close").format(hu_name), title=_("Weight Required"))
    if weighed:
        values["gross_weight"] = flt(gross_weight)
        calculated = flt(frappe.db.get_value("Handling Unit", hu_name, "gross_weight"))
        tolerance = flt(wc.weight_tolerance_percent)
        if tolerance and calculated and abs(flt(gross_weight) - calculated) / calculated * 100 > tolerance:
            frappe.throw(_("Weighed {0} is more than {1}% away from the calculated {2} - check the contents before closing").format(
                flt(gross_weight), tolerance, calculated), title=_("Weight Out of Tolerance"))
    deliveries = hu_deliveries(hu_name)
    if wc.completeness_check in ("Warn", "Block") and not int(confirm_incomplete or 0):
        pending = {d: _unpacked_for_delivery(wc, d, hu_name) for d in deliveries}
        pending = {d: hus for d, hus in pending.items() if hus}
        if pending:
            text = "; ".join(_("{0}: still in {1}").format(d, ", ".join(h)) for d, h in pending.items())
            if wc.completeness_check == "Block":
                frappe.throw(_("Delivery not completely packed - {0}").format(text), title=_("Completeness Check"))
            return {"handling_unit": hu_name, "needs_confirmation": 1, "message": _("Delivery not completely packed - {0}. Close anyway?").format(text)}
    frappe.get_doc("Handling Unit", hu_name, for_update=True)
    frappe.db.set_value("Handling Unit", hu_name, values)
    for child in hu_subtree(hu_name)[1:]:
        frappe.db.set_value("Handling Unit", child, {"closed": 1, "status": "Closed"})
    _event(hu_name, "Closed", wc)
    spool = create_print_spool("Handling Unit", hu_name, "HU Closed", wc.warehouse) if wc.print_label_on_close else None
    for delivery in deliveries:
        _update_packing_status(delivery)
    destination = move_to_bin
    if not destination:
        if wc.close_follow_up == "Move to Outbound Section": destination = wc.outbound_section_bin
        elif wc.close_follow_up == "Move to Delivery Staging Bin" and len(deliveries) == 1:
            destination = frappe.db.get_value("Outbound Delivery", next(iter(deliveries)), "staging_bin")
    moved = None
    if destination and destination != wc.bin:
        relocate_handling_unit(hu_name, destination)
        moved = destination
    return {"handling_unit": hu_name, "print_spool": spool, "moved_to": moved}


def reopen_hu(work_center, hu_name):
    wc = _station(work_center, "allow_close_hu")
    hu = _hu_at_station(wc, hu_name)
    if not hu.closed: frappe.throw(_("{0} is not closed").format(hu_name))
    for name in hu_subtree(hu_name):
        frappe.db.set_value("Handling Unit", name, {"closed": 0, "status": "Open"})
    _event(hu_name, "Reopened", wc)
    for delivery in hu_deliveries(hu_name):
        _update_packing_status(delivery)
    return {"handling_unit": hu_name}


def _update_packing_status(delivery):
    """Packed once every HU still holding this delivery's stock is closed (as a whole)."""
    from frappe_wms.services.shipping import delivery_handling_units
    tops = delivery_handling_units([delivery])
    if not tops: return
    closed = all(frappe.db.get_value("Handling Unit", t, "closed") for t in tops)
    frappe.db.set_value("Outbound Delivery", delivery, "packing_status", "Packed" if closed else "In Process")
