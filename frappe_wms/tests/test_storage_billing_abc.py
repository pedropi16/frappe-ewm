import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from frappe_wms.services.billing import generate_billing_for_period
from frappe_wms.services.slotting import classify_abc
from frappe_wms.services.storage_billing import snapshot_storage_usage
from frappe_wms.tests.bootstrap import TEST_CUSTOMER, TEST_ITEM


class TestStorageBillingAndAbc(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        self.uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        if not frappe.db.exists("WMS Product", TEST_ITEM):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        self.wh = f"SB-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": self.wh, "warehouse_name": self.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": self.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage",
                        "capacity_check_method": "HU Count", "hu_managed": 0, "active": 1}).insert(ignore_permissions=True)
        self.bin = frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{self.wh}-B1", "warehouse": self.wh, "storage_type": f"{self.wh}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True).name

    def test_per_diem_storage_is_snapshotted_and_billed_to_the_owners_customer(self):
        owner = "SB-OWNER-" + self.wh
        frappe.get_doc({"doctype": "WMS Stock Owner", "owner_code": owner, "owner_name": owner, "partner_type": "Customer", "partner": TEST_CUSTOMER, "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WMS Stock Balance", "__newname": frappe.generate_hash(length=10), "product": TEST_ITEM, "storage_bin": self.bin, "warehouse": self.wh,
                        "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 7, "available_quantity": 7, "stock_owner": owner}).insert(ignore_permissions=True)
        for back in (0, 1):
            snapshot_storage_usage(add_days(nowdate(), -back))
        snapshot_storage_usage(nowdate())  # idempotent per day
        self.assertEqual(frappe.db.count("Storage Usage Day", {"warehouse": self.wh}), 2)
        frappe.get_doc({"doctype": "Billing Rate", "priority": 1, "warehouse": self.wh, "customer": TEST_CUSTOMER, "activity": "Storage", "uom_basis": "Per Unit Day",
                        "rate": 0.5, "billing_item": TEST_ITEM, "active": 1}).insert(ignore_permissions=True)
        [line] = generate_billing_for_period(self.wh, TEST_CUSTOMER, add_days(nowdate(), -1), nowdate())
        self.assertEqual((line["activity"], line["billed_quantity"], line["charge"]), ("Storage", 14, 7))

    def test_abc_classes_follow_pick_share(self):
        self.assertEqual(classify_abc(self.wh), [], "no picks, no classes")
        items = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=2, order_by="creation asc", pluck="name")
        for item in items:
            if not frappe.db.exists("WMS Product", item):
                frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": frappe.db.get_value("Item", item, "stock_uom"), "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        for item, picks in ((items[0], 9), (items[1], 1)):
            for _i in range(picks):
                frappe.get_doc({"doctype": "WMS Stock Ledger Entry", "posting_datetime": frappe.utils.now_datetime(), "warehouse": self.wh, "product": item, "storage_bin": self.bin,
                                "stock_type": "AVAILABLE", "quantity": -1, "stock_uom": self.uom, "movement_type": "401", "reference_doctype": "WMS Warehouse", "reference_name": self.wh,
                                "idempotency_key": frappe.generate_hash(length=12), "posting_user": "Administrator"}).insert(ignore_permissions=True)
        result = classify_abc(self.wh, apply=1)
        self.assertEqual([(r.product, r["abc"]) for r in result], [(items[0], "A"), (items[1], "B")])
        self.assertEqual(frappe.db.get_value("WMS Product", items[0], "abc_indicator"), "A")
