"""Fixtures for the scanner-app browser tests (tests/e2e). Idempotent - safe to run before every suite.

    bench --site wms.local execute frappe_wms.tests.e2e.seed.run

Creates an operator + an admin user with known passwords, an E2E warehouse with bins, two WMS Resources and an item with a
scannable barcode. Everything is namespaced "E2E" so it never touches real data.
"""
import json

import frappe

WAREHOUSE = "E2E-WH"
BINS = ["E2E-WH-A1", "E2E-WH-A2", "E2E-WH-B1", "E2E-WH-RECV", "E2E-WH-STAGE"]
OPERATOR = ("e2e.operator@example.test", "E2e-Operator-1!")
ADMIN = ("e2e.admin@example.test", "E2e-Admin-1!")
BARCODE = "5901234123457"
RESOURCES = ["E2E-RF1", "E2E-RF2"]


def _user(email, password, roles):
    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
    else:
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split(".")[1].split("@")[0].title(), "last_name": "E2E",
                               "send_welcome_email": 0, "user_type": "System User", "enabled": 1})
    have = {r.role for r in user.roles}
    for role in roles:
        if role not in have and frappe.db.exists("Role", role):
            user.append("roles", {"role": role})
    user.new_password = password
    user.flags.ignore_password_policy = True
    user.save(ignore_permissions=True)


def run():
    frappe.set_user("Administrator")
    company = frappe.get_all("Company", limit=1, pluck="name")[0]
    # order_by="creation asc": with no explicit order this defaults to newest-first, which any
    # ad-hoc test-created stock item (even a throwaway one from a spec elsewhere) would then keep
    # winning over the long-standing baseline item every future run - oldest-first is stable.
    # Not batch/serial-managed: these specs post receipts/moves without a batch or serial, so on a
    # site whose oldest stock item happens to be batch-managed every such spec failed for that
    # reason alone (seen running this suite on a site that also holds the tests/load DC seed).
    item = frappe.get_all("Item", filters={"is_stock_item": 1, "has_batch_no": 0, "has_serial_no": 0}, order_by="creation asc", limit=1, pluck="name")[0]

    _user(*OPERATOR, roles=["WMS Operator", "Desk User"])
    _user(*ADMIN, roles=["System Manager", "WMS Administrator", "WMS Supervisor", "WMS Operator", "Stock Manager", "Item Manager"])

    if not frappe.db.exists("WMS Warehouse", WAREHOUSE):
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": WAREHOUSE, "warehouse_name": WAREHOUSE, "company": company,
                        "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
    st = f"{WAREHOUSE}-ST"
    if not frappe.db.exists("Storage Type", st):
        frappe.get_doc({"doctype": "Storage Type", "warehouse": WAREHOUSE, "storage_type_code": "ST", "storage_type_name": "E2E storage",
                        "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
    gr = f"{WAREHOUSE}-GR"
    if not frappe.db.exists("Storage Type", gr):
        frappe.get_doc({"doctype": "Storage Type", "warehouse": WAREHOUSE, "storage_type_code": "GR", "storage_type_name": "E2E receiving", "storage_role": "Receiving",
                        "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
    frappe.db.set_value("Storage Type", st, "hu_managed", 0)  # so an ad-hoc Move needs no destination Handling Unit
    for i, code in enumerate(BINS, 1):
        if not frappe.db.exists("Storage Bin", code):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": code, "warehouse": WAREHOUSE, "storage_type": st, "active": 1, "sequence": i}).insert(ignore_permissions=True)
    for code in ("E2E-WH-RECV", "E2E-WH-STAGE"):
        frappe.db.set_value("Storage Bin", code, "storage_type", gr)
    wh = frappe.get_doc("WMS Warehouse", WAREHOUSE)
    if not wh.default_receiving_bin:
        wh.default_receiving_bin = "E2E-WH-RECV"; wh.default_shipping_bin = "E2E-WH-STAGE"; wh.default_difference_bin = "E2E-WH-RECV"
        wh.save(ignore_permissions=True)
    if not frappe.db.exists("Bin Determination Rule", {"warehouse": WAREHOUSE, "activity": "Putaway"}):
        frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": WAREHOUSE, "activity": "Putaway", "active": 1, "priority": 1,
                        "destination_storage_type": st, "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
    if not frappe.db.exists("Handling Unit Type", "E2E-PALLET"):
        frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "E2E-PALLET", "hu_type_name": "E2E pallet"}).insert(ignore_permissions=True)
    if not frappe.db.exists("WMS Product", {"item": item}):
        frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": frappe.db.get_value("Item", item, "stock_uom"), "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
    # The barcode must resolve to *this* item - a previous run may have attached it to another one.
    for row in frappe.get_all("Item Barcode", filters={"barcode": BARCODE, "parent": ["!=", item]}, pluck="name"):
        frappe.db.delete("Item Barcode", row)
    if not frappe.db.exists("Item Barcode", {"barcode": BARCODE}):
        doc = frappe.get_doc("Item", item)
        doc.append("barcodes", {"barcode": BARCODE})
        doc.save(ignore_permissions=True)
    for code in RESOURCES:
        if not frappe.db.exists("WMS Resource", code):
            frappe.get_doc({"doctype": "WMS Resource", "resource_code": code, "warehouse": WAREHOUSE, "resource_type": "Scanner", "active": 1}).insert(ignore_permissions=True)
        frappe.db.set_value("WMS Resource", code, {"user": None, "logged_in_at": None, "current_queue": None})
    if not frappe.db.exists("WMS Exception Code", "E2E-DAMAGED"):
        frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": "E2E-DAMAGED", "exception_name": "E2E damaged goods", "category": "Task", "active": 1,
                        "requires_comment": 1, "follow_up_action": "None", "allowed_task_types": [{"task_type": "Internal Move"}]}).insert(ignore_permissions=True)
    from frappe.core.doctype.user.user import generate_keys
    keys = generate_keys(ADMIN[0])
    api_key = frappe.db.get_value("User", ADMIN[0], "api_key")
    frappe.db.commit()
    info = {"api_key": api_key, "api_secret": keys.get("api_secret"), "warehouse": WAREHOUSE, "bins": BINS, "item": item, "uom": frappe.db.get_value("Item", item, "stock_uom"), "barcode": BARCODE,
            "operator": OPERATOR[0], "operator_password": OPERATOR[1], "admin": ADMIN[0], "admin_password": ADMIN[1], "resources": RESOURCES}
    print("E2E_SEED " + json.dumps(info))
    return info


def cleanup():
    """Removes tasks/documents the browser tests created in the E2E warehouse (raw deletes - test data only)."""
    frappe.set_user("Administrator")
    frappe.db.sql("delete from `tabStock Allocation` where stock_balance like 'E2EMON%%'")
    for dt in ("Handling Unit Event",):
        frappe.db.sql(f"delete from `tab{dt}` where handling_unit like 'E2EPC%%' or handling_unit like 'E2EH%%'")
    frappe.db.sql("delete from `tabHandling Unit` where hu_number like 'E2EPC%%' or hu_number like 'E2EH%%'")
    for dt in ("Warehouse Task", "WMS Stock Ledger Entry", "WMS Stock Balance", "WMS Physical Inventory Count"):
        frappe.db.sql(f"delete from `tab{dt}` where warehouse=%s", WAREHOUSE)
    frappe.db.commit()


def monitor_stock():
    """Stock Overview fixture: 6 serial balances of one product in one bin (two of them allocated) plus one
    plain row - so grouping has something to collapse. cleanup() removes them."""
    frappe.set_user("Administrator")
    item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
    uom = frappe.db.get_value("Item", item, "stock_uom")
    bin_ = f"{WAREHOUSE}-A1" if frappe.db.exists("Storage Bin", f"{WAREHOUSE}-A1") else frappe.get_all("Storage Bin", filters={"warehouse": WAREHOUSE}, limit=1, pluck="name")[0]
    for i in range(6):
        frappe.get_doc({"doctype": "WMS Stock Balance", "name": f"E2EMON{i}", "warehouse": WAREHOUSE, "product": item, "storage_bin": bin_,
                        "serial_no": f"E2E-SN-{i}", "stock_type": "AVAILABLE", "quantity": 1, "allocated_quantity": 1 if i < 2 else 0,
                        "available_quantity": 0 if i < 2 else 1, "stock_uom": uom,
                        "first_receipt_date": "2026-10-01 08:30:00"}).insert(ignore_permissions=True, ignore_links=True)
        if i < 2:
            frappe.get_doc({"doctype": "Stock Allocation", "outbound_delivery": "E2E-OBD-X", "outbound_delivery_item": "E2E-ITEM-X", "product": item, "stock_balance": f"E2EMON{i}",
                            "storage_bin": bin_, "serial_no": f"E2E-SN-{i}", "stock_type": "AVAILABLE", "allocated_quantity": 1, "picked_quantity": 0,
                            "status": "Allocated"}).insert(ignore_permissions=True, ignore_links=True)
    frappe.db.commit()
    print("E2E_MONITOR " + json.dumps({"bin": bin_}))


def packing_stock():
    """Packing Center fixture: two empty-ish HUs in bin A1 (6 units of the item in the first), an internally
    numbered HU type with a packing material, and a number range - cleanup() removes the HUs and stock."""
    from frappe_wms.services.stock import post_entries
    frappe.set_user("Administrator")
    item = frappe.get_all("Item", filters={"is_stock_item": 1, "has_batch_no": 0, "has_serial_no": 0}, order_by="creation asc", limit=1, pluck="name")[0]
    uom = frappe.db.get_value("Item", item, "stock_uom")
    for code, mode in (("E2E-PAL", "External"), ("E2E-BOX", "Internal")):
        if not frappe.db.exists("Handling Unit Type", code):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": code, "hu_type_name": code, "numbering_mode": mode, "nestable": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("Packaging Material", "E2E-CARTON"):
        frappe.get_doc({"doctype": "Packaging Material", "packaging_material_code": "E2E-CARTON", "packaging_material_name": "E2E carton", "hu_type": "E2E-BOX", "active": 1}).insert(ignore_permissions=True)
    if not frappe.db.exists("WMS Number Range", {"range_for": "Handling Unit", "warehouse": WAREHOUSE}):
        frappe.get_doc({"doctype": "WMS Number Range", "range_for": "Handling Unit", "warehouse": WAREHOUSE, "prefix": "E2EH", "start_number": 1, "end_number": 99999,
                        "number_length": 6, "current_number": 0, "active": 1}).insert(ignore_permissions=True)
    bin_ = f"{WAREHOUSE}-A1"
    if frappe.db.exists("Storage Bin", f"{WAREHOUSE}-B1"):
        frappe.db.set_value("WMS Warehouse", WAREHOUSE, "default_difference_bin", f"{WAREHOUSE}-B1")
    hus = []
    for n in ("E2EPC1", "E2EPC2"):
        name = frappe.db.get_value("Handling Unit", {"hu_number": n}) or frappe.get_doc({"doctype": "Handling Unit", "hu_number": n, "hu_type": "E2E-PAL",
            "warehouse": WAREHOUSE, "current_bin": bin_, "status": "Open"}).insert(ignore_permissions=True).name
        hus.append(name)
    post_entries([{"warehouse": WAREHOUSE, "product": item, "storage_bin": bin_, "handling_unit": hus[0], "stock_type": "AVAILABLE", "stock_uom": uom,
                   "quantity": 6, "movement_type": "701"}], "Storage Bin", bin_, f"e2e-pc:{frappe.generate_hash(length=8)}")
    frappe.db.commit()
    print("E2E_PACKING " + json.dumps({"hus": hus, "item": item}))
