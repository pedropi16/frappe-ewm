import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime
from frappe_wms.services.stock import transfer_stock
from frappe_wms.services.determination import determine_route
from frappe_wms.services.numbering import next_number
from frappe_wms.services.task import my_resource
from frappe_wms.services.issue import post_goods_issue_for_delivery
from frappe_wms.services.printing import create_print_spool
from frappe_wms.utils import require_role

LOAD_ROLES = ("WMS Operator", "WMS Loader", "WMS Supervisor")
OPEN_SHIPMENT_STATUSES = ("Planned", "Released", "Staging", "Ready to Load", "Loading")

def cross_dock_handling_units(outbound_delivery=None, outbound_delivery_item=None):
    # Cross-docked stock never has a Stock Allocation or a Pick task: it is staged for the delivery
    # by a Cross Dock task raised straight from the Goods Receipt (services/receipt.py), whose
    # Warehouse Request points back at the delivery (and line). Without this, a delivery filled by
    # cross-docking had "no staged Handling Unit" and could never be shipped or issued -
    # reproduced in a simulated shift once a Cross Dock queue was configured.
    filters = {"request_type": "Cross Dock", "reference_doctype": "Outbound Delivery"}
    if outbound_delivery: filters["reference_name"] = ["in", outbound_delivery] if isinstance(outbound_delivery, (list, tuple, set)) else outbound_delivery
    if outbound_delivery_item: filters["reference_line"] = outbound_delivery_item
    requests = frappe.get_all("Warehouse Request", filters=filters, pluck="name")
    if not requests: return []
    return frappe.get_all("Warehouse Task", filters={"warehouse_request": ["in", requests], "task_type": "Cross Dock",
        "status": "Confirmed", "destination_hu": ["is", "set"]}, pluck="destination_hu", distinct=True)

def delivery_handling_units(delivery_names):
    """The top-level HUs that currently hold stock for these deliveries - what a shipment loads.

    Stock reaches a delivery's HUs three ways: a confirmed Pick task's destination_hu (Stock
    Allocation.handling_unit is only the pre-pick source), a Cross Dock task's destination_hu,
    and a carton packed for the delivery at a packing station (Handling Unit.outbound_delivery,
    see services/packing_station.py). A pick HU emptied by repacking holds nothing any more and
    drops out; a carton packed inside a pallet is shipped as that pallet."""
    from frappe_wms.services.packing_station import subtree_quantity, top_hu
    hus = set(cross_dock_handling_units(outbound_delivery=list(delivery_names)))
    allocation_names = frappe.get_all("Stock Allocation", filters={"outbound_delivery": ["in", delivery_names]}, pluck="name")
    task_names = frappe.get_all("Warehouse Task Allocation", filters={"stock_allocation": ["in", allocation_names]}, pluck="parent") if allocation_names else []
    if task_names:
        tasks = frappe.get_all("Warehouse Task",
            filters={"name": ["in", task_names], "task_type": "Pick", "status": "Confirmed", "destination_hu": ["is", "set"]},
            fields=["destination_hu"], distinct=True)
        hus |= {t.destination_hu for t in tasks}
    hus |= set(frappe.get_all("Handling Unit", filters={"outbound_delivery": ["in", list(delivery_names)],
        "status": ["not in", ["Shipped", "Cancelled"]]}, pluck="name"))
    return sorted({top_hu(hu) for hu in hus if subtree_quantity(hu) > 0.000001})

def _other_deliveries_sharing_hu(hu_name, deliveries_being_shipped):
    # A confirmed Pick task's destination_hu defaults to its own source_hu when no distinct
    # destination was scanned (the RF Pick screen's own documented default) - correct for a
    # whole-HU pick, but when a source HU has enough stock to (partially) satisfy more than one
    # Outbound Delivery, more than one delivery's pick can legitimately resolve to that *same*
    # physical HU. Shipping it on this shipment alone would then claim it exclusively (Handling
    # Unit.status/.shipment aren't per-delivery), stranding whatever the other delivery still
    # needs from it: reproduced with two deliveries allocated off one bulk pallet, each shipped
    # independently - the second could never post its Goods Issue once the first shipment's HU
    # moved past "Loaded" to "Shipped". Returns the other delivery name(s) still owed stock from
    # this HU - or from anything nested in it, e.g. a carton another delivery was packed into -
    # that aren't part of *this* shipment, so the caller can refuse with an actionable message
    # instead of silently mis-shipping.
    from frappe_wms.services.packing_station import hu_deliveries
    return sorted(hu_deliveries(hu_name) - set(deliveries_being_shipped))

def create_shipment(warehouse, outbound_deliveries, carrier=None, route=None, vehicle_registration=None, driver_name=None):
    require_role(*LOAD_ROLES)
    if not outbound_deliveries: frappe.throw(_("At least one Outbound Delivery is required"))
    deliveries = frappe.get_all("Outbound Delivery", filters={"name": ["in", outbound_deliveries]},
        fields=["name", "warehouse", "picking_status", "status"])
    if len(deliveries) != len(outbound_deliveries): frappe.throw(_("One or more Outbound Deliveries were not found"))
    for d in deliveries:
        if d.warehouse != warehouse: frappe.throw(_("Delivery {0} does not belong to warehouse {1}").format(d.name, warehouse))
        if d.picking_status != "Picked": frappe.throw(_("Delivery {0} is not fully picked yet").format(d.name))
        if frappe.db.exists("Shipment Delivery", {"outbound_delivery": d.name, "parenttype": "WMS Shipment",
                "parent": ["in", frappe.get_all("WMS Shipment", filters={"status": ["in", OPEN_SHIPMENT_STATUSES]}, pluck="name") or [""]]}):
            frappe.throw(_("Delivery {0} is already on an open shipment").format(d.name))

    route = route or determine_route(warehouse, carrier=carrier)
    if not route: frappe.throw(_("No Route could be determined for warehouse {0}; configure one or pass route explicitly").format(warehouse))

    hus = delivery_handling_units(outbound_deliveries)
    if not hus: frappe.throw(_("None of these deliveries have a staged Handling Unit yet"))
    # The deliveries come in stop order (1 = first consignee). The truck is loaded in reverse -
    # the last stop's HUs go in first, deepest - so every stop unloads from the back.
    stop_of = {name: i for i, name in enumerate(outbound_deliveries, 1)}
    delivery_of = {}
    for name in outbound_deliveries:
        for hu in delivery_handling_units([name]): delivery_of.setdefault(hu, name)
    hus = sorted(hus, key=lambda hu: (-stop_of.get(delivery_of.get(hu), 0), hus.index(hu)))
    delivery_set = set(outbound_deliveries)
    for hu in hus:
        others = _other_deliveries_sharing_hu(hu, delivery_set)
        if others:
            frappe.throw(_("Handling Unit {0} is also picked for {1}, which {2} not included in this shipment - "
                "ship them together, or pick this delivery into a different Handling Unit").format(
                hu, ", ".join(others), _("is") if len(others) == 1 else _("are")))

    shipment = frappe.get_doc({
        "doctype": "WMS Shipment", "shipment_number": next_number("WMS Shipment", warehouse=warehouse),
        "warehouse": warehouse, "route": route, "carrier": carrier,
        "vehicle_registration": vehicle_registration, "driver_name": driver_name, "status": "Ready to Load",
        "deliveries": [{"outbound_delivery": name, "stop_sequence": stop_of[name]} for name in outbound_deliveries],
        "handling_units": [{"handling_unit": hu, "load_sequence": i, "loaded": 0, "outbound_delivery": delivery_of.get(hu),
                            "stop_sequence": stop_of.get(delivery_of.get(hu))} for i, hu in enumerate(hus, 1)],
    })
    shipment.insert(ignore_permissions=True)
    frappe.db.set_value("Outbound Delivery", {"name": ["in", outbound_deliveries]}, {"status": "Loading", "loading_status": "In Process"})
    return shipment.name

def list_loadable_shipments(user=None):
    require_role(*LOAD_ROLES)
    resource = my_resource(user)
    filters = {"status": ["in", ("Ready to Load", "Loading")]}
    if resource: filters["warehouse"] = resource.warehouse
    shipments = frappe.get_list("WMS Shipment", filters=filters,
        fields=["name", "shipment_number", "warehouse", "route", "door", "staging_bin", "status"], order_by="creation asc", limit=50)
    for s in shipments:
        rows = frappe.get_all("Shipment Handling Unit", filters={"parent": s.name}, fields=["handling_unit", "loaded", "load_sequence", "outbound_delivery", "stop_sequence"],
                              order_by="load_sequence asc")
        s["handling_units"] = rows
        s["loaded_count"] = sum(1 for r in rows if r.loaded)
        s["total_count"] = len(rows)
    return shipments

def find_shipment_for(reference, user=None):
    """Resolves a scanned Outbound Delivery or Handling Unit straight to its open shipment - a
    loader may already be holding the HU, or only know the delivery/shipment number, and
    shouldn't have to browse the shipment list to find which door/truck it's on."""
    require_role(*LOAD_ROLES)
    resource = my_resource(user)
    filters = {"status": ["in", ("Ready to Load", "Loading")]}
    if resource: filters["warehouse"] = resource.warehouse
    open_shipments = frappe.get_all("WMS Shipment", filters=filters, pluck="name")
    if not open_shipments: return None
    if reference in open_shipments: return reference
    by_number = frappe.get_all("WMS Shipment", filters={"name": ["in", open_shipments], "shipment_number": reference}, pluck="name", limit=1)
    if by_number: return by_number[0]
    by_hu = frappe.get_all("Shipment Handling Unit", filters={"parent": ["in", open_shipments], "handling_unit": reference}, pluck="parent", limit=1)
    if by_hu: return by_hu[0]
    by_delivery = frappe.get_all("Shipment Delivery", filters={"parent": ["in", open_shipments], "outbound_delivery": reference}, pluck="parent", limit=1)
    return by_delivery[0] if by_delivery else None

def _route_hops(route_name, door_bin):
    # An HU travels the route's ordered stops (e.g. a marshalling/consolidation bin for
    # cross-dock, a yard checkpoint) before it reaches the shipment's door - the physical path
    # SAP EWM calls out separately from the single "staging bin" concept. No stops configured
    # collapses to the original direct staging-bin-to-door hop.
    stops = frappe.get_all("WMS Route Stop", filters={"parent": route_name}, fields=["storage_bin"], order_by="sequence asc")
    hops = [s.storage_bin for s in stops if s.storage_bin != door_bin]
    hops.append(door_bin)
    return hops

def confirm_hu_loaded(shipment_name, hu_name, confirm_out_of_sequence=0):
    require_role(*LOAD_ROLES)
    shipment = frappe.get_doc("WMS Shipment", shipment_name, for_update=True)
    if shipment.status not in ("Ready to Load", "Loading"): frappe.throw(_("Shipment is not open for loading"))
    from frappe_wms.services.yard import gate_check
    gate_check(shipment.warehouse, shipment=shipment_name)
    row = next((r for r in shipment.handling_units if r.handling_unit == hu_name), None)
    if not row: frappe.throw(_("Handling Unit {0} is not on this shipment").format(hu_name))
    if row.loaded: frappe.throw(_("Handling Unit {0} is already loaded").format(hu_name))
    check = frappe.db.get_value("WMS Warehouse", shipment.warehouse, "load_sequence_check") or "Off"
    if check != "Off" and row.stop_sequence:
        deeper = [r for r in shipment.handling_units if not r.loaded and cint(r.stop_sequence) > cint(row.stop_sequence)]
        if deeper:
            message = _("{0} is for stop {1}, but {2} HU(s) for a later stop must go into the truck first (next: {3}, stop {4})").format(
                hu_name, row.stop_sequence, len(deeper), deeper[0].handling_unit, deeper[0].stop_sequence)
            if check == "Block": frappe.throw(message)
            if not cint(confirm_out_of_sequence): return {"needs_confirmation": message}
    door_bin = shipment.door or shipment.staging_bin
    if not door_bin: frappe.throw(_("Shipment has no door or staging bin configured"))
    hops = _route_hops(shipment.route, door_bin) if shipment.route else [door_bin]
    _advance_hu_through_hops(hu_name, hops, "WMS Shipment", shipment.name)
    frappe.db.set_value("Shipment Handling Unit", row.name, "loaded", 1)
    all_rows = frappe.get_all("Shipment Handling Unit", filters={"parent": shipment.name}, fields=["loaded"])
    fully_loaded = all(r.loaded for r in all_rows)
    shipment.db_set("status", "Loaded" if fully_loaded else "Loading", update_modified=True)
    if fully_loaded:
        delivery_names = frappe.get_all("Shipment Delivery", filters={"parent": shipment.name}, pluck="outbound_delivery")
        frappe.db.set_value("Outbound Delivery", {"name": ["in", delivery_names]}, {"status": "Loaded", "loading_status": "Loaded"})
        create_print_spool("WMS Shipment", shipment.name, "Shipment Loaded", shipment.warehouse)
        for delivery_name in delivery_names:
            _auto_post_goods_issue(delivery_name)
    return {"shipment": shipment.name, "handling_unit": hu_name, "shipment_status": "Loaded" if fully_loaded else "Loading"}

def _auto_post_goods_issue(delivery_name):
    # Mirrors SAP EWM: Goods Issue posts on its own the moment a delivery finishes loading,
    # instead of waiting for someone to tap "Post Goods Issue" separately. Not every line is
    # necessarily ready yet (e.g. a delivery split across two shipments, only one of which just
    # finished loading) - that's the ordinary "nothing to issue yet" case, not a real failure, so
    # it's swallowed here; the loader's HU is loaded either way and this simply gets retried the
    # next time a HU for this delivery is loaded. A savepoint means a failed attempt (of any
    # kind) rolls back cleanly instead of leaving a half-inserted Goods Issue behind - this must
    # never block the loading confirmation that got us here.
    savepoint = f"auto_goods_issue_{frappe.generate_hash(length=8)}"
    frappe.db.savepoint(savepoint)
    try:
        post_goods_issue_for_delivery(delivery_name)
    except frappe.ValidationError:
        frappe.db.rollback(save_point=savepoint)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(title=f"Auto Goods Issue failed for {delivery_name}")

def _advance_hu_through_hops(hu_name, hops, reference_doctype, reference_name):
    # Walks the HU one route stop at a time instead of teleporting it straight to the door, so
    # every intermediate stop leaves a real ledger posting and Handling Unit Event - each hop
    # uses the movement type of the matching Warehouse Process Type (OB_STAGE for an
    # intermediate stop, OB_LOAD for the final hop into the door) rather than a hardcoded code.
    current_bin = frappe.db.get_value("Handling Unit", hu_name, "current_bin")
    # Never skip the final hop outright, even if the HU is already sitting there (a
    # single-hop route with no intermediate stops, most commonly): it's still the one that
    # must mark the HU "Loaded", which _relocate_handling_unit now does even with nothing to
    # physically move. Only intermediate hops already passed are safe to skip.
    start = min(hops.index(current_bin) + 1, len(hops) - 1) if current_bin in hops else 0
    for i in range(start, len(hops)):
        is_final = i == len(hops) - 1
        process_type = frappe.get_cached_doc("Warehouse Process Type", "OB_LOAD" if is_final else "OB_STAGE")
        _relocate_handling_unit(hu_name, hops[i], process_type.movement_type, reference_doctype, reference_name,
            event_type="Loaded" if is_final else "Staged", hu_status="Loaded" if is_final else "Staged")

def _relocate_handling_unit(hu_name, destination_bin, movement_type, reference_doctype, reference_name, event_type, hu_status):
    hu = frappe.get_doc("Handling Unit", hu_name)
    source_bin = hu.current_bin
    # An HU already sitting at this hop (a route with no intermediate stops between staging and
    # the door, or the pick task's own destination already being the door bin - both real,
    # supported configurations, not edge cases) needs no stock movement, but it still needs its
    # own status/event recorded: skipping that here left the HU stuck at "Staged" even though the
    # Shipment/Outbound Delivery correctly advanced to Loaded, which then broke Goods Issue -
    # _loaded_handling_units_for_line requires the HU's own status to actually say "Loaded".
    if source_bin != destination_bin:
        balances = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu_name, "storage_bin": source_bin, "quantity": [">", 0]},
            fields=["product", "batch_no", "serial_no", "stock_type", "quantity", "stock_uom"])
        for i, balance in enumerate(balances, 1):
            source = {"warehouse": hu.warehouse, "product": balance.product, "batch_no": balance.batch_no, "serial_no": balance.serial_no,
                "handling_unit": hu_name, "storage_bin": source_bin, "stock_type": balance.stock_type, "stock_uom": balance.stock_uom}
            destination = {"handling_unit": hu_name, "storage_bin": destination_bin, "stock_type": balance.stock_type}
            transfer_stock(source=source, destination=destination, quantity=balance.quantity, movement_type=movement_type,
                reference_doctype=reference_doctype, reference_name=reference_name,
                idempotency_key=f"{event_type.upper()}:{reference_name}:{hu_name}:{destination_bin}:{i}")
    hu.flags.wms_service_update = True
    hu.current_bin = destination_bin
    hu.status = hu_status
    if event_type == "Loaded" and reference_doctype == "WMS Shipment": hu.shipment = reference_name
    hu.save(ignore_permissions=True)
    frappe.get_doc({"doctype": "Handling Unit Event", "handling_unit": hu_name, "event_type": event_type,
        "bin_before": source_bin, "bin_after": destination_bin, "reference_doctype": reference_doctype,
        "reference_name": reference_name, "event_timestamp": now_datetime(), "performed_by": frappe.session.user}).insert(ignore_permissions=True)
    for child in frappe.get_all("Handling Unit", filters={"parent_hu": hu_name}, pluck="name"):
        _relocate_handling_unit(child, destination_bin, movement_type, reference_doctype, reference_name, event_type, hu_status)

def depart_shipment(shipment_name):
    # LOAD_ROLES, not Supervisor-only: the RF Load screen offers "Depart" to whoever just loaded
    # the last HU, and a WMS Loader tapping it got "You are not permitted to perform this
    # warehouse operation" - the truck is loaded and closed either way.
    require_role(*LOAD_ROLES)
    shipment = frappe.get_doc("WMS Shipment", shipment_name)
    if shipment.status != "Loaded": frappe.throw(_("Shipment must be fully loaded before it can depart"))
    shipment.db_set({"status": "Departed", "actual_departure": now_datetime()}, update_modified=True)
    from frappe_wms.services.yard import on_shipment_departed
    on_shipment_departed(shipment.name)
    return {"shipment": shipment.name, "status": "Departed"}

def complete_shipment(shipment_name):
    # Closes out the shipment lifecycle (e.g. proof-of-delivery received) once it has departed.
    # This is a logistics milestone only - it doesn't touch Outbound Delivery / Goods Issue,
    # which is posted independently once a delivery is picked (see services/issue.py).
    require_role("WMS Supervisor")
    shipment = frappe.get_doc("WMS Shipment", shipment_name)
    if shipment.status != "Departed": frappe.throw(_("Shipment must have departed before it can be completed"))
    shipment.db_set("status", "Completed", update_modified=True)
    hu_names = frappe.get_all("Handling Unit", filters={"shipment": shipment.name}, pluck="name")
    if hu_names: frappe.db.set_value("Handling Unit", {"name": ["in", hu_names]}, "status", "Shipped")
    return {"shipment": shipment.name, "status": "Completed"}
