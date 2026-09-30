import frappe
from frappe import _
from frappe.utils import flt
from frappe_wms.services.stock import post_entries
from frappe_wms.services.task import my_resource
from frappe_wms.services.printing import create_print_spool
from frappe_wms.utils import require_role, storage_bin_role

READY_TO_SHIP_STATUSES = ("Picking", "Picked", "Packing", "Packed", "Staging", "Staged", "Loading", "Loaded")

def post_goods_issue(doc):
    if frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype":doc.doctype,"reference_name":doc.name}): return
    for i,row in enumerate(doc.items,1):
        hu=frappe.get_doc("Handling Unit",row.handling_unit)
        if hu.status != "Loaded": frappe.throw(_("HU {0} must be loaded (via a Shipment) before Goods Issue can be posted").format(hu.name))
        # Mirrors SAP EWM: Goods Issue may only be posted out of a Door bin - configure which
        # Storage Bins are doors via their Storage Type's "Door" role (see WMS Route/Shipment
        # default_door, which are validated against the same role on save).
        if storage_bin_role(hu.current_bin) != "Door":
            frappe.throw(_("HU {0} is in bin {1}, which is not configured as a Door - Goods Issue can only be posted from a Door bin").format(hu.name, hu.current_bin))
        # WMS Product.serial_control is a per-product setting, not a global switch - "None" (the
        # default) means this item is never expected to carry a serial at all, and most items
        # should stay that way. "Required at Receipt"/"Always" were already enforced on the way
        # in (services/receipt.py); "Required at Issue" and the issue side of "Always" were
        # declared in the doctype's own option list but never actually checked anywhere,
        # confirmed by grep - a product configured to require a serial on the way OUT could ship
        # with none at all.
        product = frappe.get_cached_doc("WMS Product", row.item) if frappe.db.exists("WMS Product", row.item) else None
        if product and product.warehouse_managed and product.serial_control in ("Required at Issue", "Always") and not row.serial_no:
            frappe.throw(_("Row {0}: {1} requires a serial number at issue").format(row.idx, row.item))
        # The HU's actual current bin (the door it was loaded to), not the delivery's staging
        # bin - loading may have moved it on since staging, and the stock ledger only has a
        # balance wherever the HU physically is now.
        entry={"warehouse":doc.warehouse,"product":row.item,"batch_no":row.batch_no,"serial_no":row.serial_no,"handling_unit":row.handling_unit,"storage_bin":hu.current_bin,"stock_type":row.stock_type,"quantity":-row.quantity,"stock_uom":row.stock_uom,"movement_type":"601","reference_line":row.name}
        post_entries([entry],doc.doctype,doc.name,f"GI:{doc.name}:{i}")
        hu.flags.wms_service_update=True; hu.status="Shipped"; hu.save(ignore_permissions=True)
        if row.outbound_delivery_item:
            current = flt(frappe.db.get_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity"))
            frappe.db.set_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity", current + flt(row.quantity))
    doc.db_set("status","Posted")
    _update_delivery_issue_status(doc.outbound_delivery)
    create_print_spool("Goods Issue", doc.name, "Goods Issue Posted", doc.warehouse)

def reverse_goods_issue(doc):
    original=frappe.get_all("WMS Stock Ledger Entry",filters={"reference_doctype":doc.doctype,"reference_name":doc.name,"reversal_of":["in",[None,""]]},fields=["*"])
    if not original: return
    hus=set()
    for i,row in enumerate(original,1):
        values={k:row.get(k) for k in ("warehouse","product","batch_no","serial_no","handling_unit","storage_bin","stock_type","stock_uom")}
        values.update({"quantity":-row.quantity,"movement_type":"602","reversal_of":row.name})
        post_entries([values],doc.doctype,doc.name,f"GI-REV:{doc.name}:{i}")
        if row.handling_unit: hus.add(row.handling_unit)
    for hu_name in hus:
        hu=frappe.get_doc("Handling Unit",hu_name)
        hu.flags.wms_service_update=True; hu.status="Staged"; hu.save(ignore_permissions=True)
    for row in doc.items:
        if row.outbound_delivery_item:
            current = flt(frappe.db.get_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity"))
            frappe.db.set_value("Outbound Delivery Item", row.outbound_delivery_item, "issued_quantity", max(current - flt(row.quantity), 0))
    doc.db_set({"status":"Reversed","reversed":1})
    _update_delivery_issue_status(doc.outbound_delivery)

def _update_delivery_issue_status(delivery_name):
    if not delivery_name: return
    rows = frappe.get_all("Outbound Delivery Item", filters={"parent": delivery_name}, fields=["requested_quantity", "issued_quantity"])
    if not rows: return
    fully_issued = all(flt(r.issued_quantity) >= flt(r.requested_quantity) for r in rows)
    any_issued = any(flt(r.issued_quantity) > 0 for r in rows)
    goods_issue_status = "Posted" if fully_issued else ("Partially Posted" if any_issued else "Not Posted")
    # Keep status in sync in both directions: a reversal that drops a delivery below fully
    # issued must not leave it stuck on "Goods Issued".
    values = {"goods_issue_status": goods_issue_status, "status": "Goods Issued" if fully_issued else "Staged"}
    frappe.db.set_value("Outbound Delivery", delivery_name, values)

def _loaded_handling_units_for_line(outbound_delivery_item):
    # Stock Allocation.handling_unit is the pre-pick source HU, not where the line actually
    # ended up - only a confirmed Pick task's destination_hu records the real HU it was staged
    # into (see the identical walk in services/shipping.py). Goods Issue additionally requires
    # that HU to have since been loaded onto a Shipment (see post_goods_issue), so only a
    # destination_hu whose current status is "Loaded" is offered up here.
    #
    # A line's picked quantity can legitimately span more than one physical HU - a normal
    # allocation outcome whenever one delivery's need is filled from more than one source
    # pallet/bin - so this returns every loaded one, not just the first.
    # Cross-docked stock reaches the line through a Cross Dock task instead (see
    # shipping.cross_dock_handling_units).
    from frappe_wms.services.shipping import cross_dock_handling_units
    destination_hus = set(cross_dock_handling_units(outbound_delivery_item=outbound_delivery_item))
    allocation_names = frappe.get_all("Stock Allocation",
        filters={"outbound_delivery_item": outbound_delivery_item, "status": ["in", ["Picked", "Partially Picked"]]}, pluck="name")
    task_names = frappe.get_all("Warehouse Task Allocation", filters={"stock_allocation": ["in", allocation_names]}, pluck="parent") if allocation_names else []
    if task_names:
        destination_hus |= set(frappe.get_all("Warehouse Task",
            filters={"name": ["in", task_names], "task_type": "Pick", "status": "Confirmed", "destination_hu": ["is", "set"]},
            pluck="destination_hu", distinct=True))
    # Cartons packed for the delivery at a packing station, and anything nested in a loaded HU
    # (loading cascades "Loaded" down to every nested HU - services/shipping.py).
    from frappe_wms.services.packing_station import hu_subtree
    delivery = frappe.db.get_value("Outbound Delivery Item", outbound_delivery_item, "parent")
    destination_hus |= set(frappe.get_all("Handling Unit", filters={"outbound_delivery": delivery}, pluck="name"))
    candidates = {sub for hu in destination_hus for sub in hu_subtree(hu)}
    return sorted({hu for hu in candidates if frappe.db.get_value("Handling Unit", hu, "status") == "Loaded"})

def _ready_lines_for_delivery(delivery_name):
    # Each line still owing a goods issue, split across however many loaded HUs it actually
    # takes to cover it (handling_unit_splits) - shared by the RF Ship screen (list_ready_to_ship)
    # and the Monitor's one-tap post_goods_issue_for_delivery, so both agree on what's ready.
    # suggested_handling_unit (the first split, if any) is kept only for whatever already reads
    # it as a single-value hint.
    rows = frappe.get_all("Outbound Delivery Item", filters={"parent": delivery_name},
        fields=["name", "item", "picked_quantity", "issued_quantity", "stock_uom", "required_stock_type"])
    lines = []
    used = {}  # (hu, product, stock_type) -> already split to an earlier line of this delivery
    for row in rows:
        remaining = flt(row.picked_quantity) - flt(row.issued_quantity)
        if remaining <= 0: continue
        row["remaining_quantity"] = remaining
        splits = []
        need = remaining
        for hu_name in _loaded_handling_units_for_line(row.name):
            if need <= 0: break
            bin_name = frappe.db.get_value("Handling Unit", hu_name, "current_bin")
            on_hand = flt(frappe.db.get_value("WMS Stock Balance", {
                "handling_unit": hu_name, "storage_bin": bin_name, "product": row.item, "stock_type": row.required_stock_type,
            }, "quantity"))
            key = (hu_name, row.item, row.required_stock_type)
            on_hand -= used.get(key, 0)
            if on_hand <= 0: continue
            take = min(need, on_hand)
            splits.append({"handling_unit": hu_name, "quantity": take})
            used[key] = used.get(key, 0) + take
            need -= take
        row["handling_unit_splits"] = splits
        row["suggested_handling_unit"] = splits[0]["handling_unit"] if splits else None
        lines.append(row)
    return lines

def list_ready_to_ship(user=None):
    resource = my_resource(user)
    filters = {"picking_status": "Picked", "goods_issue_status": ["!=", "Posted"], "status": ["in", READY_TO_SHIP_STATUSES]}
    if resource: filters["warehouse"] = resource.warehouse
    deliveries = frappe.get_list("Outbound Delivery", filters=filters,
        fields=["name", "outbound_delivery_number", "warehouse", "customer", "staging_bin", "route", "door", "status", "delivery_date"],
        order_by="delivery_date asc, creation asc", limit=50)
    for delivery in deliveries:
        delivery["items"] = _ready_lines_for_delivery(delivery.name)
    return [d for d in deliveries if d["items"]]

def create_and_submit_goods_issue(outbound_delivery, items):
    # items: [{outbound_delivery_item, item, quantity, stock_uom, handling_unit, stock_type}]
    require_role("WMS Operator", "WMS Loader", "WMS Supervisor")
    delivery = frappe.get_doc("Outbound Delivery", outbound_delivery)
    if not items: frappe.throw(_("At least one issue line is required"))
    gi = frappe.get_doc({
        "doctype": "Goods Issue", "outbound_delivery": delivery.name, "warehouse": delivery.warehouse,
        "staging_bin": delivery.staging_bin, "items": items,
    })
    gi.insert(ignore_permissions=True)
    gi.flags.ignore_permissions = True
    gi.submit()
    return {"goods_issue": gi.name}

def post_goods_issue_for_delivery(delivery_name):
    # The Monitor's one-tap "Post Goods Issue" - auto-builds the same payload the RF Ship
    # screen's per-line form would, one Goods Issue line per loaded HU a delivery line's
    # quantity is actually split across (see _ready_lines_for_delivery) rather than assuming a
    # single HU covers all of it - that assumption used to throw "Insufficient stock" for
    # whatever a line's first HU didn't happen to hold, once allocation legitimately split it
    # across a second one (reproduced via the load-test generator at moderate volume).
    require_role("WMS Operator", "WMS Loader", "WMS Supervisor")
    lines = _ready_lines_for_delivery(delivery_name)
    if not lines: frappe.throw(_("Nothing left to issue for this delivery"))
    missing = [l.item for l in lines if not l.handling_unit_splits]
    if missing: frappe.throw(_("No loaded Handling Unit found for: {0}. Load it onto a Shipment first.").format(", ".join(missing)))
    items = [
        {"outbound_delivery_item": l.name, "item": l.item, "quantity": s["quantity"],
         "stock_uom": l.stock_uom, "handling_unit": s["handling_unit"], "stock_type": l.required_stock_type}
        for l in lines for s in l.handling_unit_splits
    ]
    result = create_and_submit_goods_issue(delivery_name, items)
    # A line whose loaded HUs don't yet cover its full remaining quantity still gets whatever is
    # actually ready issued now (a normal partial state - goods_issue_status already models
    # "Partially Posted") instead of failing the whole call; flag it so the caller can tell.
    short = [l.item for l in lines if sum(s["quantity"] for s in l.handling_unit_splits) < l.remaining_quantity - 0.000001]
    if short: result["partially_issued"] = short
    return result
