"""Load/stress test generator for a real, already-configured WMS Warehouse (defaults to DC1).

Creates tagged users and master data, then drives a configurable number of randomized inbound
and outbound cycles through the same API layer the RF app and Monitor call (not shortcuts into
the ledger directly), so it exercises the real code paths: Purchase Order -> Inbound Delivery ->
Goods Receipt -> Putaway, and Sales Order -> Outbound Delivery -> Allocation -> Pick -> Shipment
-> auto Goods Issue. Every cycle randomly stops partway (task left open, or an exception raised)
instead of always running to completion, so the result looks like a real, messy, in-progress
warehouse - which is the point: something for the Monitor to actually show, and a chance for a
latent bug to surface, not a clean demo. One failed cycle never aborts the run; every outcome is
logged into the returned report.

Everything created is tagged (LOADTEST-... names, loadtest.*@example.test emails) so cleanup()
can remove it later without touching anything else. Nothing here touches configuration
(warehouses, storage types, bins, rules) - only master data (Items/Customer/Supplier/Users) and
the transactional documents those drive.

    bench --site <site> execute frappe_wms.tests.load.generate.run \\
      --kwargs "{'warehouse': 'DC1', 'inbound_cycles': 30, 'outbound_cycles': 20}"

    bench --site <site> execute frappe_wms.tests.load.generate.cleanup --kwargs "{'warehouse': 'DC1'}"

Pass seed=<int> for a reproducible run.
"""
import random

import frappe
from frappe.utils import nowdate

from frappe_wms.services.allocation import NON_ALLOCATABLE_STORAGE_ROLES, allocate_delivery
from frappe_wms.services.handling_unit import get_or_create_handling_unit  # noqa: F401 (kept importable for callers/tests)
from frappe_wms.services.procurement import create_inbound_delivery_from_purchase_order
from frappe_wms.services.receipt import create_and_submit_goods_receipt
from frappe_wms.services.sales import create_outbound_delivery_from_sales_order
from frappe_wms.services.shipping import create_shipment, confirm_hu_loaded
from frappe_wms.services.task import create_pick_tasks, confirm_task, raise_exception

TAG = "LOADTEST"
ITEM_COUNT = 8
USER_COUNT = 6
EXCEPTION_CODES = ("DAMAGED", "WRONG_ITEM", "BIN_BLOCKED")
QUANTITIES = (5, 10, 20, 50, 100, 250)


def _tagged_items():
    return [f"{TAG}-ITEM-{i:02d}" for i in range(1, ITEM_COUNT + 1)]


def _tagged_users():
    return [f"{TAG.lower()}.operator{i}@example.test" for i in range(1, USER_COUNT + 1)]


def ensure_master_data(warehouse):
    """Idempotent - creates only what's missing. Returns the tagged master data by name."""
    frappe.set_user("Administrator")
    company = frappe.get_all("Company", limit=1, pluck="name")
    if not company: frappe.throw("No Company exists on this site yet - create one first")
    company = company[0]

    supplier = f"{TAG}-SUPPLIER"
    if not frappe.db.exists("Supplier", supplier):
        frappe.get_doc({"doctype": "Supplier", "supplier_name": supplier, "supplier_type": "Company"}).insert(ignore_permissions=True)

    customer = f"{TAG}-CUSTOMER"
    if not frappe.db.exists("Customer", customer):
        frappe.get_doc({"doctype": "Customer", "customer_name": customer, "customer_type": "Company"}).insert(ignore_permissions=True)

    item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or frappe.db.get_value("Item Group", {}, "name")
    for code in _tagged_items():
        if frappe.db.exists("Item", code): continue
        frappe.get_doc({
            "doctype": "Item", "item_code": code, "item_name": code, "item_group": item_group,
            "stock_uom": "Nos", "is_stock_item": 1,
        }).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": code}):
            frappe.get_doc({"doctype": "WMS Product", "item": code, "stock_uom": "Nos", "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)

    users = []
    for i, email in enumerate(_tagged_users(), 1):
        password = f"{TAG.title()}-Op{i}-1!"
        if not frappe.db.exists("User", email):
            user = frappe.get_doc({
                "doctype": "User", "email": email, "first_name": f"{TAG.title()} Operator {i}",
                "send_welcome_email": 0, "user_type": "System User", "enabled": 1,
            })
            for role in ("WMS Operator", "WMS Picker", "WMS Receiver", "WMS Loader", "Desk User"):
                if frappe.db.exists("Role", role): user.append("roles", {"role": role})
            user.new_password = password
            user.flags.ignore_password_policy = True
            user.insert(ignore_permissions=True)
        users.append({"email": email, "password": password})
        resource = f"{TAG}-RF{i}"
        if not frappe.db.exists("WMS Resource", resource):
            frappe.get_doc({
                "doctype": "WMS Resource", "resource_code": resource, "warehouse": warehouse,
                "resource_type": "Scanner", "active": 1,
            }).insert(ignore_permissions=True)
    frappe.db.commit()
    erpnext_warehouse = frappe.db.get_value("WMS Warehouse", warehouse, "erpnext_warehouse")
    if not erpnext_warehouse: frappe.throw(f"WMS Warehouse {warehouse} has no linked ERPNext Warehouse yet")
    return {"company": company, "supplier": supplier, "customer": customer, "items": _tagged_items(),
            "users": users, "erpnext_warehouse": erpnext_warehouse}


def _inbound_cycle(warehouse, company, supplier, item, erpnext_warehouse, report):
    qty = random.choice(QUANTITIES)
    po = frappe.get_doc({
        "doctype": "Purchase Order", "supplier": supplier, "company": company, "schedule_date": nowdate(),
        "items": [{"item_code": item, "qty": qty, "rate": 10, "schedule_date": nowdate(), "warehouse": erpnext_warehouse}],
    })
    po.insert(ignore_permissions=True)
    po.submit()
    ind = frappe.get_doc("Inbound Delivery", create_inbound_delivery_from_purchase_order(po.name, warehouse))
    hu_number = f"{TAG}-{frappe.generate_hash(length=10)}"
    result = create_and_submit_goods_receipt(ind.name, [{
        "inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty,
        "stock_uom": ind.items[0].stock_uom, "handling_unit": hu_number, "stock_type": ind.items[0].expected_stock_type,
    }])
    report["created"].append(("Goods Receipt", result["goods_receipt"], f"{qty} x {item}"))
    outcome = random.random()
    for task_name in result["warehouse_tasks"]:
        if outcome < 0.1:
            raise_exception(task_name, random.choice(EXCEPTION_CODES), remarks="Load test: simulated exception")
            report["exceptions"].append(("Warehouse Task", task_name))
        elif outcome < 0.8:
            confirm_task(task_name, confirmed_quantity=qty)
            report["confirmed"].append(("Warehouse Task", task_name))
        else:
            report["left_open"].append(("Warehouse Task", task_name))


def _available_tagged_stock(warehouse, items):
    """Product -> available quantity, restricted to bins allocation.py itself would consider
    allocatable - so a cycle only ever tries to sell what a real pick could actually find."""
    rows = frappe.get_all("WMS Stock Balance", filters={
        "warehouse": warehouse, "product": ["in", items], "available_quantity": [">", 0],
    }, fields=["product", "available_quantity", "storage_bin"])
    if not rows: return {}
    bin_roles = {b.name: b.storage_role for b in frappe.get_all(
        "Storage Bin", filters={"name": ["in", list({r.storage_bin for r in rows if r.storage_bin})]},
        fields=["name", "storage_type.storage_role as storage_role"],
    )}
    totals = {}
    for r in rows:
        if bin_roles.get(r.storage_bin) in NON_ALLOCATABLE_STORAGE_ROLES: continue
        totals[r.product] = totals.get(r.product, 0) + r.available_quantity
    return {k: v for k, v in totals.items() if v > 0}


def _outbound_cycle(warehouse, company, customer, item, available_qty, erpnext_warehouse, report):
    qty = min(random.choice(QUANTITIES), available_qty)
    if qty <= 0: return
    so = frappe.get_doc({
        "doctype": "Sales Order", "customer": customer, "company": company, "delivery_date": nowdate(),
        "items": [{"item_code": item, "qty": qty, "rate": 15, "delivery_date": nowdate(), "warehouse": erpnext_warehouse}],
    })
    so.insert(ignore_permissions=True)
    so.submit()
    delivery_name = create_outbound_delivery_from_sales_order(so.name, warehouse)
    delivery = frappe.get_doc("Outbound Delivery", delivery_name)
    delivery.submit()
    report["created"].append(("Outbound Delivery", delivery_name, f"{qty} x {item}"))

    allocated = allocate_delivery(delivery_name)
    if not allocated:
        report["left_open"].append(("Outbound Delivery (nothing allocated)", delivery_name))
        return
    tasks = create_pick_tasks(delivery_name)
    if not tasks:
        report["left_open"].append(("Outbound Delivery (no pick tasks)", delivery_name))
        return

    outcome = random.random()
    if outcome < 0.1:
        raise_exception(tasks[0], random.choice(EXCEPTION_CODES), remarks="Load test: simulated exception")
        report["exceptions"].append(("Warehouse Task", tasks[0]))
        for t in tasks[1:]:
            confirm_task(t, confirmed_quantity=frappe.db.get_value("Warehouse Task", t, "planned_quantity"))
            report["confirmed"].append(("Warehouse Task", t))
        return
    if outcome < 0.25:
        # Partial pick - a realistic "still in progress" state for the Monitor to show.
        for t in tasks[: max(1, len(tasks) // 2)]:
            confirm_task(t, confirmed_quantity=frappe.db.get_value("Warehouse Task", t, "planned_quantity"))
            report["confirmed"].append(("Warehouse Task", t))
        report["left_open"].append(("Outbound Delivery (partially picked)", delivery_name))
        return

    for t in tasks:
        confirm_task(t, confirmed_quantity=frappe.db.get_value("Warehouse Task", t, "planned_quantity"))
        report["confirmed"].append(("Warehouse Task", t))
    delivery.reload()
    if delivery.picking_status != "Picked":
        report["left_open"].append(("Outbound Delivery (picking incomplete)", delivery_name))
        return
    shipment_name = create_shipment(warehouse, [delivery_name])
    report["created"].append(("WMS Shipment", shipment_name, None))
    shipment = frappe.get_doc("WMS Shipment", shipment_name)
    for row in shipment.handling_units:
        confirm_hu_loaded(shipment_name, row.handling_unit)
    # confirm_hu_loaded auto-posts Goods Issue once every HU on the shipment is loaded
    # (services/shipping.py._auto_post_goods_issue) but deliberately swallows its own failures -
    # report honestly whether it actually landed rather than masking a real miss with a second,
    # differently-behaved call of our own.
    if frappe.db.get_value("Outbound Delivery", delivery_name, "goods_issue_status") == "Posted":
        report["created"].append(("Goods Issue (auto-posted)", delivery_name, None))
    else:
        report["failed"].append(("auto goods issue did not post", delivery_name, None))


def run(warehouse="DC1", inbound_cycles=30, outbound_cycles=20, seed=None):
    frappe.set_user("Administrator")
    # See the matching comment in cleanup(): this many documents/users created in a tight loop
    # can enqueue more background jobs than a lightly-provisioned queue can hold; in_test makes
    # enqueue() calls run inline instead of queuing (e.g. User.on_update's create_contact).
    frappe.in_test = True
    if seed is not None:
        random.seed(seed)
    master = ensure_master_data(warehouse)
    report = {"created": [], "confirmed": [], "left_open": [], "exceptions": [], "failed": [], "skipped": []}

    for i in range(inbound_cycles):
        item = random.choice(master["items"])
        frappe.db.savepoint(f"loadtest_in_{i}")
        try:
            _inbound_cycle(warehouse, master["company"], master["supplier"], item, master["erpnext_warehouse"], report)
            frappe.db.commit()
        except Exception as e:
            frappe.db.rollback(save_point=f"loadtest_in_{i}")
            report["failed"].append(("inbound_cycle", item, str(e)))

    for i in range(outbound_cycles):
        stock = _available_tagged_stock(warehouse, master["items"])
        if not stock:
            report["skipped"].append(("outbound_cycle", "no allocatable stock yet"))
            continue
        item = random.choice(list(stock.keys()))
        frappe.db.savepoint(f"loadtest_out_{i}")
        try:
            _outbound_cycle(warehouse, master["company"], master["customer"], item, stock[item], master["erpnext_warehouse"], report)
            frappe.db.commit()
        except Exception as e:
            frappe.db.rollback(save_point=f"loadtest_out_{i}")
            report["failed"].append(("outbound_cycle", item, str(e)))

    summary = {k: len(v) for k, v in report.items()}
    print(f"Load test done: {summary}")
    if report["failed"]:
        print("Failures (this is the point - these are the problems to look at):")
        for row in report["failed"]:
            print(" -", row)
    report["summary"] = summary
    report["users"] = master["users"]
    return report


def _force_delete(doctype, name):
    """Best-effort delete for cleanup(): tries the normal cancel-then-delete path (so a document
    that's fine to remove that way leaves no orphaned linked records), but this data is disposable
    by design - a business rule blocking cancellation (e.g. a delivery whose Goods Issue already
    posted, correctly enforced by test_cancel_blocked_while_goods_issue_is_posted) must not leave
    load-test junk stuck forever, so a raw SQL delete is the guaranteed fallback."""
    savepoint = f"force_delete_{frappe.generate_hash(length=8)}"
    frappe.db.savepoint(savepoint)
    try:
        doc = frappe.get_doc(doctype, name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.db.sql(f"delete from `tab{doctype}` where name=%s", name)


def cleanup(warehouse="DC1"):
    """Removes every document this generator created (by warehouse + tagged master data), then
    the tagged master data and users themselves. Safe to run even if nothing was ever generated."""
    frappe.set_user("Administrator")
    # frappe.delete_doc enqueues a background job (dynamic-link cleanup) per call by default -
    # deleting this many documents in a tight loop can outrun a lightly-provisioned queue
    # (worker capacity, not an app bug). in_test makes enqueue() run everything inline instead.
    frappe.in_test = True
    items = _tagged_items()

    for dt in ("Warehouse Task", "WMS Stock Ledger Entry", "WMS Stock Balance"):
        frappe.db.sql(f"delete from `tab{dt}` where warehouse=%s", warehouse)
    frappe.db.sql("delete from `tabStock Allocation` where product in %s", (items,))
    frappe.db.sql("delete from `tabWarehouse Request` where warehouse=%s", warehouse)
    frappe.db.sql("delete from `tabWarehouse Order` where warehouse=%s", warehouse)
    for dt in ("WMS Shipment", "Outbound Delivery", "Goods Issue", "Goods Receipt", "Inbound Delivery"):
        for name in frappe.get_all(dt, filters={"warehouse": warehouse}, pluck="name"):
            _force_delete(dt, name)
    # Sales/Purchase Orders referencing a tagged item: child-table filter needs its own query.
    for parent_dt, child_dt in (("Sales Order", "Sales Order Item"), ("Purchase Order", "Purchase Order Item")):
        names = frappe.get_all(child_dt, filters={"item_code": ["in", items]}, pluck="parent", distinct=True)
        for name in set(names):
            if frappe.db.exists(parent_dt, name):
                _force_delete(parent_dt, name)

    for code in _tagged_items():
        if frappe.db.exists("WMS Product", {"item": code}):
            frappe.delete_doc("WMS Product", frappe.db.get_value("WMS Product", {"item": code}, "name"), ignore_permissions=True, force=True)
        if frappe.db.exists("Item", code):
            frappe.delete_doc("Item", code, ignore_permissions=True, force=True)
    for name in (f"{TAG}-SUPPLIER", f"{TAG}-CUSTOMER"):
        for dt in ("Supplier", "Customer"):
            if frappe.db.exists(dt, name):
                frappe.delete_doc(dt, name, ignore_permissions=True, force=True)
    for i in range(1, USER_COUNT + 1):
        resource = f"{TAG}-RF{i}"
        if frappe.db.exists("WMS Resource", resource):
            frappe.delete_doc("WMS Resource", resource, ignore_permissions=True, force=True)
    for email in _tagged_users():
        if frappe.db.exists("User", email):
            frappe.delete_doc("User", email, ignore_permissions=True, force=True)

    frappe.db.commit()
    print("Load test data cleaned up.")
