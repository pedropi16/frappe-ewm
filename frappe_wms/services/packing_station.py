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

def _station(work_center):
    require_role(*PACKING_ROLES)
    wc = frappe.db.get_value("Work Center", work_center, ["name", "warehouse", "bin", "active", "work_center_name"], as_dict=True)
    if not wc: frappe.throw(_("Work Center {0} not found").format(work_center))
    if not wc.active: frappe.throw(_("Work Center {0} is not active").format(work_center))
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
    """Which delivery the destination HU will hold after this pack step, refusing any mix."""
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


# ---------------------------------------------------------------- read side

def list_work_centers(warehouse=None):
    require_role(*PACKING_ROLES)
    filters = {"active": 1}
    if warehouse: filters["warehouse"] = warehouse
    return frappe.get_all("Work Center", filters=filters, fields=["name", "work_center_code", "work_center_name", "warehouse", "bin"], order_by="work_center_code asc")


def _node(hu_name):
    hu = frappe.db.get_value("Handling Unit", hu_name, ["name", "hu_number", "hu_type", "status", "stock_status", "closed", "gross_weight",
                                                        "net_weight", "tare_weight", "volume", "outbound_delivery", "parent_hu", "sscc"], as_dict=True)
    hu["stock"] = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu_name, "quantity": [">", 0]},
                                 fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"], order_by="product asc")
    hu["children"] = [_node(c) for c in frappe.get_all("Handling Unit", filters={"parent_hu": hu_name}, pluck="name", order_by="hu_number asc")]
    hu["deliveries"] = sorted(hu_deliveries(hu_name)) if hu.parent_hu is None else []
    return hu


def station_overview(work_center):
    wc = _station(work_center)
    tops = frappe.get_all("Handling Unit", filters={"current_bin": wc.bin, "parent_hu": ["in", ["", None]],
                                                    "status": ["not in", ["Shipped", "Cancelled"]]}, pluck="name", order_by="modified desc")
    loose = frappe.get_all("WMS Stock Balance", filters={"storage_bin": wc.bin, "handling_unit": ["in", ["", None]], "quantity": [">", 0]},
                           fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"], order_by="product asc")
    orders = frappe.get_all("Packing Order", filters={"work_center_bin": wc.bin, "status": ["in", ["Draft", "Open", "In Process"]]},
                            fields=["name", "outbound_delivery", "status"], order_by="creation asc")
    for o in orders:
        o["source_hus"] = frappe.get_all("Packing Source HU", filters={"parent": o.name}, pluck="handling_unit")
        o["destination_hus"] = frappe.get_all("Packing Destination HU", filters={"parent": o.name}, pluck="handling_unit")
    nodes = [_node(n) for n in tops]
    delivery_names = sorted({d for n in nodes for d in n["deliveries"]} | {o.outbound_delivery for o in orders if o.outbound_delivery})
    deliveries = {d.name: d for d in frappe.get_all("Outbound Delivery", filters={"name": ["in", delivery_names]},
                  fields=["name", "outbound_delivery_number", "customer", "route", "staging_bin", "door", "packing_status"])} if delivery_names else {}
    return {"work_center": wc, "handling_units": nodes, "loose_stock": loose, "packing_orders": orders, "deliveries": deliveries,
            "hu_types": frappe.get_all("Handling Unit Type", filters={"active": 1}, fields=["name", "hu_type_name", "numbering_mode", "category"])}


# ---------------------------------------------------------------- write side

def pack_product(work_center, product, quantity, destination_hu, source_hu=None, stock_type=None, batch_no=None, serial_no=None,
                 outbound_delivery=None, idempotency_key=None):
    """Moves `quantity` of one product from a source HU (or loose on the table) into the
    destination HU. The SAP "Pack Product" tab: scan source, scan product, quantity, scan target."""
    wc = _station(work_center)
    quantity = flt(quantity)
    if quantity <= 0: frappe.throw(_("Enter a quantity greater than zero"))
    if source_hu and source_hu == destination_hu: frappe.throw(_("Source and destination are the same Handling Unit"))
    dest = _hu_at_station(wc, destination_hu, _("Destination HU"))
    _require_open(dest)
    if source_hu:
        src = _hu_at_station(wc, source_hu, _("Source HU"))
        if src.closed: frappe.throw(_("Source HU {0} is closed - reopen it to take stock out").format(source_hu))
    filters = {"storage_bin": wc.bin, "handling_unit": source_hu or ["in", ["", None]], "product": product, "quantity": [">", 0]}
    for k, v in (("stock_type", stock_type), ("batch_no", batch_no), ("serial_no", serial_no)):
        if v: filters[k] = v
    balances = frappe.get_all("WMS Stock Balance", filters=filters, fields=["product", "batch_no", "serial_no", "stock_type", "stock_uom", "quantity"])
    where = source_hu or _("loose on the table")
    if not balances: frappe.throw(_("No {0} in {1}").format(product, where))
    if len({(b.batch_no or "", b.serial_no or "", b.stock_type) for b in balances}) > 1:
        what = _("serial number") if any(b.serial_no for b in balances) else _("batch") if len({b.batch_no for b in balances}) > 1 else _("stock type")
        frappe.throw(_("{0} in {1} has more than one {2} - scan or choose which one").format(product, where, what))
    bal = balances[0]
    if quantity - flt(bal.quantity) > EPS:
        frappe.throw(_("Only {0} {1} of {2} in {3}").format(flt(bal.quantity), bal.stock_uom, product, where))
    target = _resolve_delivery(hu_deliveries(source_hu) if source_hu else set(), destination_hu, outbound_delivery)
    frappe.get_doc("Handling Unit", destination_hu, for_update=True)
    repack_loose(wc.bin, source_hu, destination_hu, [{"item": product, "quantity": quantity, "stock_uom": bal.stock_uom,
                 "stock_type": bal.stock_type, "batch_no": bal.batch_no, "serial_no": bal.serial_no}],
                 "Work Center", wc.name, idempotency_key or f"PACK:{wc.name}:{frappe.generate_hash(length=12)}")
    _stamp_delivery(destination_hu, target)
    return {"destination_hu": destination_hu, "product": product, "quantity": quantity, "outbound_delivery": target}


def pack_all(work_center, source_hu, destination_hu, outbound_delivery=None, idempotency_key=None):
    """Everything in the source HU (not its nested HUs) into the destination - "Repack all"."""
    wc = _station(work_center)
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


def pack_hu(work_center, hu_name, destination_hu, outbound_delivery=None):
    """Puts a whole HU (with everything inside it) into another HU - SAP "Repack HU"."""
    wc = _station(work_center)
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
    wc = _station(work_center)
    hu = _hu_at_station(wc, hu_name)
    if not hu.parent_hu: frappe.throw(_("{0} is not packed inside another Handling Unit").format(hu_name))
    parent = frappe.db.get_value("Handling Unit", hu.parent_hu, ["name", "closed", "status"], as_dict=True)
    if parent.closed: frappe.throw(_("{0} is closed - reopen it first").format(parent.name))
    unnest_handling_unit(hu_name)
    return {"handling_unit": hu_name}


def create_station_hu(work_center, hu_type, hu_number=None, outbound_delivery=None):
    wc = _station(work_center)
    hu = create_handling_unit(hu_number or None, hu_type, storage_bin=wc.bin, warehouse=wc.warehouse)
    if outbound_delivery:
        frappe.db.set_value("Handling Unit", hu["name"], "outbound_delivery", outbound_delivery)
    return {"name": hu["name"], "hu_number": hu.get("hu_number"), "hu_type": hu_type}


def close_hu(work_center, hu_name, gross_weight=None, move_to_bin=None):
    """SAP "Close HU": records the weighed gross weight, stops further packing, queues the HU
    label (a WMS Print Determination Rule for event "HU Closed"), and optionally moves the
    closed HU straight on (e.g. to the delivery's staging bin)."""
    wc = _station(work_center)
    hu = _hu_at_station(wc, hu_name)
    if hu.closed: frappe.throw(_("{0} is already closed").format(hu_name))
    if hu.parent_hu: frappe.throw(_("{0} is packed inside {1} - close the outer HU instead").format(hu_name, hu.parent_hu))
    if subtree_quantity(hu_name) <= EPS: frappe.throw(_("{0} is empty - nothing to close").format(hu_name))
    values = {"closed": 1, "status": "Closed"}
    if gross_weight not in (None, ""):
        if flt(gross_weight) <= 0: frappe.throw(_("Gross weight must be greater than zero"))
        values["gross_weight"] = flt(gross_weight)
        net = flt(frappe.db.get_value("Handling Unit", hu_name, "net_weight"))
        if net and flt(gross_weight) < net:
            # Not blocking - the scale is the truth on the floor - but a gross below the
            # product master's net weight means one of the two is wrong.
            frappe.msgprint(_("Weighed gross {0} is below the calculated net weight {1} - check the scale or the product weights")
                            .format(flt(gross_weight), net), indicator="orange", alert=True)
    frappe.get_doc("Handling Unit", hu_name, for_update=True)
    frappe.db.set_value("Handling Unit", hu_name, values)
    for child in hu_subtree(hu_name)[1:]:
        frappe.db.set_value("Handling Unit", child, {"closed": 1, "status": "Closed"})
    frappe.get_doc({"doctype": "Handling Unit Event", "handling_unit": hu_name, "event_type": "Closed", "bin_after": wc.bin,
                    "reference_doctype": "Work Center", "reference_name": wc.name, "event_timestamp": now_datetime(),
                    "performed_by": frappe.session.user}).insert(ignore_permissions=True)
    spool = create_print_spool("Handling Unit", hu_name, "HU Closed", wc.warehouse)
    for delivery in hu_deliveries(hu_name):
        _update_packing_status(delivery)
    if move_to_bin and move_to_bin != wc.bin:
        relocate_handling_unit(hu_name, move_to_bin)
    return {"handling_unit": hu_name, "print_spool": spool, "moved_to": move_to_bin if move_to_bin and move_to_bin != wc.bin else None}


def reopen_hu(work_center, hu_name):
    wc = _station(work_center)
    hu = _hu_at_station(wc, hu_name)
    if not hu.closed: frappe.throw(_("{0} is not closed").format(hu_name))
    for name in hu_subtree(hu_name):
        frappe.db.set_value("Handling Unit", name, {"closed": 0, "status": "Open"})
    frappe.get_doc({"doctype": "Handling Unit Event", "handling_unit": hu_name, "event_type": "Reopened", "bin_after": wc.bin,
                    "reference_doctype": "Work Center", "reference_name": wc.name, "event_timestamp": now_datetime(),
                    "performed_by": frappe.session.user}).insert(ignore_permissions=True)
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
