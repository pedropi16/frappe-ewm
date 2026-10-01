"""before_tests hook: make the suite runnable against a genuinely fresh site.

The test classes all lean on a handful of shared master records by convention
(`frappe.get_all("Company", limit=1)[0]`, the first stock Item, a Supplier, a Customer, ...)
rather than creating their own - which only ever worked on a long-lived dev site where those
already existed. On a brand-new site (exactly what .github/workflows/ci.yml builds) 58 of 66
test classes errored in setUpClass with IndexError before running a single assertion.

Everything here is idempotent and only creates what's missing, so it's a no-op on a dev site
that already has its own data.
"""
import frappe

TEST_SUPPLIER = "_Test WMS Supplier"
TEST_CUSTOMER = "_Test WMS Customer"
TEST_ITEM = "_Test WMS Stock Item"


def _ensure_company():
    if frappe.db.get_all("Company", limit=1):
        return
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

    year = frappe.utils.getdate().year
    setup_complete({
        "language": "English", "country": "United States", "timezone": "America/New_York", "currency": "USD",
        "full_name": "Test Admin", "email": "test.admin@example.test", "company_name": "_Test WMS Company",
        "company_abbr": "_TW", "chart_of_accounts": "Standard", "fy_start_date": f"{year}-01-01",
        "fy_end_date": f"{year}-12-31", "setup_demo": 0,
    })


def _first(doctype, filters=None):
    names = frappe.get_all(doctype, filters=filters or {}, limit=1, pluck="name")
    return names[0] if names else None


def before_tests():
    frappe.set_user("Administrator")
    frappe.clear_cache()
    _ensure_company()

    # ERPNext v16 refuses has_batch_no/has_serial_no on an Item until this is on, and several
    # test classes create batch/serial-controlled items.
    if not frappe.db.get_single_value("Stock Settings", "enable_serial_and_batch_no_for_item"):
        frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)

    if not frappe.db.exists("Supplier", TEST_SUPPLIER):
        frappe.get_doc({"doctype": "Supplier", "supplier_name": TEST_SUPPLIER, "supplier_type": "Company",
                        "supplier_group": _first("Supplier Group", {"is_group": 0})}).insert(ignore_permissions=True)
    if not frappe.db.exists("Customer", TEST_CUSTOMER):
        frappe.get_doc({"doctype": "Customer", "customer_name": TEST_CUSTOMER, "customer_type": "Company",
                        "customer_group": _first("Customer Group", {"is_group": 0}),
                        "territory": _first("Territory", {"is_group": 0})}).insert(ignore_permissions=True)
    # Always present, even on a site that already has items: a plain stock item with no batch or
    # serial control, for tests that need exactly that (the first item on a seeded site may not be).
    if not frappe.db.exists("Item", TEST_ITEM):
        frappe.get_doc({"doctype": "Item", "item_code": TEST_ITEM, "item_name": TEST_ITEM, "stock_uom": "Nos",
                        "item_group": _first("Item Group", {"is_group": 0}) or "All Item Groups",
                        "is_stock_item": 1}).insert(ignore_permissions=True)
    frappe.db.commit()


def company_warehouse(company, warehouse_name):
    """Name of an ERPNext Warehouse for `company`, created (non-group, under the company's root)
    if missing - instead of hardcoding one particular dev site's abbreviation (e.g. "- TC")."""
    abbr = frappe.db.get_value("Company", company, "abbr")
    name = f"{warehouse_name} - {abbr}"
    if not frappe.db.exists("Warehouse", name):
        parent = frappe.db.get_value("Warehouse", {"company": company, "is_group": 1}, "name")
        frappe.get_doc({"doctype": "Warehouse", "warehouse_name": warehouse_name, "company": company,
                        "parent_warehouse": parent}).insert(ignore_permissions=True)
    return name


def empty_hu_like(source_hu, storage_bin):
    """A fresh, empty Handling Unit of the same HU Type as `source_hu`, sitting in `storage_bin` -
    what an operator grabs (a tote, a carton, a new pallet label) when only part of an HU's stock
    moves. confirm_task refuses to move a partial quantity "into" the source HU itself, since that
    would leave the one HU with stock in two bins."""
    from frappe_wms.services.handling_unit import create_handling_unit

    hu_type = frappe.db.get_value("Handling Unit", source_hu, "hu_type")
    warehouse = frappe.db.get_value("Storage Bin", storage_bin, "warehouse")
    return create_handling_unit(frappe.generate_hash(length=10), hu_type, storage_bin=storage_bin, warehouse=warehouse)["name"]


def pick_into_new_hu(task_name, **kwargs):
    """confirm_task for a task whose quantity is only part of its source HU: confirms it into an
    empty HU of the same type at the task's destination bin, returning (result, destination_hu)."""
    from frappe_wms.api.scanner import confirm_task

    task = frappe.db.get_value("Warehouse Task", task_name, ["source_hu", "destination_bin"], as_dict=True)
    destination_hu = empty_hu_like(task.source_hu, task.destination_bin)
    return confirm_task(task_name, destination_hu=destination_hu, **kwargs), destination_hu
