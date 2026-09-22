import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task


class TestScanVerification(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-VERIFY-WH"
        cls.bin_a = "WMS-TEST-VERIFY-WH-A"
        cls.bin_b = "WMS-TEST-VERIFY-WH-B"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.bin_a, cls.bin_b):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

    def tearDown(self):
        settings = frappe.get_single("WMS Settings")
        settings.require_scan_verification = 0
        settings.save(ignore_permissions=True)

    def _make_task(self, **overrides):
        payload = {
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 10, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        }
        payload.update(overrides)
        task = frappe.get_doc(payload)
        task.insert(ignore_permissions=True)
        return task

    def _set_verification(self, enabled):
        settings = frappe.get_single("WMS Settings")
        settings.require_scan_verification = 1 if enabled else 0
        settings.save(ignore_permissions=True)

    def test_setting_off_confirms_without_any_scan(self):
        self._set_verification(False)
        task = self._make_task()
        result = confirm_task(task.name, confirmed_quantity=10)
        self.assertEqual(result["status"], "Confirmed")

    def test_setting_on_rejects_confirmation_missing_source_scan(self):
        self._set_verification(True)
        task = self._make_task()
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, scanned_destination=self.bin_b, scanned_product=self.item, confirmed_quantity=10)

    def test_setting_on_rejects_confirmation_missing_destination_scan(self):
        self._set_verification(True)
        task = self._make_task()
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, scanned_source=self.bin_a, scanned_product=self.item, confirmed_quantity=10)

    def test_setting_on_rejects_confirmation_missing_product_scan(self):
        self._set_verification(True)
        task = self._make_task()
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, scanned_source=self.bin_a, scanned_destination=self.bin_b, confirmed_quantity=10)

    def test_setting_on_accepts_confirmation_with_all_correct_scans(self):
        self._set_verification(True)
        task = self._make_task()
        result = confirm_task(task.name, scanned_source=self.bin_a, scanned_destination=self.bin_b, scanned_product=self.item, confirmed_quantity=10)
        self.assertEqual(result["status"], "Confirmed")

    def test_wrong_product_scan_is_rejected(self):
        self._set_verification(True)
        other_item = frappe.get_all("Item", filters={"is_stock_item": 1, "name": ["!=", self.item]}, limit=1, pluck="name")
        wrong_product = other_item[0] if other_item else "NOT-THE-TASK-PRODUCT"
        task = self._make_task()
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, scanned_source=self.bin_a, scanned_destination=self.bin_b, scanned_product=wrong_product, confirmed_quantity=10)
