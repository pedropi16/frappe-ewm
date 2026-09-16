import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe_wms.services.stock import transfer_stock
from frappe_wms.services.determination import determine_route
from frappe_wms.services.numbering import next_number
from frappe_wms.services.task import my_resource
from frappe_wms.utils import require_role

LOAD_ROLES = ("WMS Operator", "WMS Loader", "WMS Supervisor")
OPEN_SHIPMENT_STATUSES = ("Planned", "Released", "Staging", "Ready to Load", "Loading")

def _staged_handling_units(delivery_names):
    # Stock Allocation.handling_unit is the pre-pick source HU, not where the line actually
    # ended up - only a confirmed Pick task's destination_hu records the real staged HU (it's
    # what _move_hu_if_complete physically relocated). Walk allocation -> task to get it right.
    allocation_names = frappe.get_all("Stock Allocation", filters={"outbound_delivery": ["in", delivery_names]}, pluck="name")
    if not allocation_names: return []
    task_names = frappe.get_all("Warehouse Task Allocation", filters={"stock_allocation": ["in", allocation_names]}, pluck="parent")
    if not task_names: return []
    tasks = frappe.get_all("Warehouse Task",
        filters={"name": ["in", task_names], "task_type": "Pick", "status": "Confirmed", "destination_hu": ["is", "set"]},
        fields=["destination_hu"], distinct=True)
    return sorted({t.destination_hu for t in tasks})

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

    hus = _staged_handling_units(outbound_deliveries)
    if not hus: frappe.throw(_("None of these deliveries have a staged Handling Unit yet"))

    shipment = frappe.get_doc({
        "doctype": "WMS Shipment", "shipment_number": next_number("WMS Shipment", warehouse=warehouse),
        "warehouse": warehouse, "route": route, "carrier": carrier,
        "vehicle_registration": vehicle_registration, "driver_name": driver_name, "status": "Ready to Load",
        "deliveries": [{"outbound_delivery": d.name} for d in deliveries],
        "handling_units": [{"handling_unit": hu, "load_sequence": i, "loaded": 0} for i, hu in enumerate(hus, 1)],
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
        rows = frappe.get_all("Shipment Handling Unit", filters={"parent": s.name}, fields=["handling_unit", "loaded", "load_sequence"], order_by="load_sequence asc")
        s["handling_units"] = rows
        s["loaded_count"] = sum(1 for r in rows if r.loaded)
        s["total_count"] = len(rows)
    return shipments

def _route_hops(route_name, door_bin):
    # An HU travels the route's ordered stops (e.g. a marshalling/consolidation bin for
    # cross-dock, a yard checkpoint) before it reaches the shipment's door - the physical path
    # SAP EWM calls out separately from the single "staging bin" concept. No stops configured
    # collapses to the original direct staging-bin-to-door hop.
    stops = frappe.get_all("WMS Route Stop", filters={"parent": route_name}, fields=["storage_bin"], order_by="sequence asc")
    hops = [s.storage_bin for s in stops if s.storage_bin != door_bin]
    hops.append(door_bin)
    return hops

def confirm_hu_loaded(shipment_name, hu_name):
    require_role(*LOAD_ROLES)
    frappe.db.sql("select name from `tabWMS Shipment` where name=%s for update", shipment_name)
    shipment = frappe.get_doc("WMS Shipment", shipment_name)
    if shipment.status not in ("Ready to Load", "Loading"): frappe.throw(_("Shipment is not open for loading"))
    row = next((r for r in shipment.handling_units if r.handling_unit == hu_name), None)
    if not row: frappe.throw(_("Handling Unit {0} is not on this shipment").format(hu_name))
    if row.loaded: frappe.throw(_("Handling Unit {0} is already loaded").format(hu_name))
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
    return {"shipment": shipment.name, "handling_unit": hu_name, "shipment_status": "Loaded" if fully_loaded else "Loading"}

def _advance_hu_through_hops(hu_name, hops, reference_doctype, reference_name):
    # Walks the HU one route stop at a time instead of teleporting it straight to the door, so
    # every intermediate stop leaves a real ledger posting and Handling Unit Event - each hop
    # uses the movement type of the matching Warehouse Process Type (OB_STAGE for an
    # intermediate stop, OB_LOAD for the final hop into the door) rather than a hardcoded code.
    current_bin = frappe.db.get_value("Handling Unit", hu_name, "current_bin")
    start = hops.index(current_bin) + 1 if current_bin in hops else 0
    for i in range(start, len(hops)):
        is_final = i == len(hops) - 1
        process_type = frappe.get_cached_doc("Warehouse Process Type", "OB_LOAD" if is_final else "OB_STAGE")
        _relocate_handling_unit(hu_name, hops[i], process_type.movement_type, reference_doctype, reference_name,
            event_type="Loaded" if is_final else "Staged", hu_status="Loaded" if is_final else "Staged")

def _relocate_handling_unit(hu_name, destination_bin, movement_type, reference_doctype, reference_name, event_type, hu_status):
    hu = frappe.get_doc("Handling Unit", hu_name)
    source_bin = hu.current_bin
    if source_bin == destination_bin: return
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
    require_role("WMS Supervisor")
    shipment = frappe.get_doc("WMS Shipment", shipment_name)
    if shipment.status != "Loaded": frappe.throw(_("Shipment must be fully loaded before it can depart"))
    shipment.db_set({"status": "Departed", "actual_departure": now_datetime()}, update_modified=True)
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
