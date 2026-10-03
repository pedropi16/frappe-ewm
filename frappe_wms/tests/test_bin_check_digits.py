import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task, verify_check_digits
from frappe_wms.utils import CHECK_DIGIT_ALPHABET


class TestBinCheckDigits(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-CHKDIGIT-WH"
        cls.bin_a = "WMS-TEST-CHKDIGIT-WH-A"
        cls.bin_b = "WMS-TEST-CHKDIGIT-WH-B"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.bin_a, cls.bin_b):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "CHKDIGIT-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "CHKDIGIT-PALLET", "hu_type_name": "Chkdigit Pallet"}).insert(ignore_permissions=True)

    def tearDown(self):
        settings = frappe.get_single("WMS Settings")
        settings.require_bin_check_digits = 0
        settings.save(ignore_permissions=True)

    def _set_check_digits(self, enabled):
        settings = frappe.get_single("WMS Settings")
        settings.require_bin_check_digits = 1 if enabled else 0
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

    def test_check_digits_auto_generate_on_insert_from_the_safe_alphabet(self):
        bin_doc = frappe.get_doc("Storage Bin", self.bin_a)
        self.assertEqual(len(bin_doc.check_digits), 2)
        self.assertTrue(all(c in CHECK_DIGIT_ALPHABET for c in bin_doc.check_digits))

    def test_verify_check_digits_matches_case_insensitively_and_rejects_wrong_value(self):
        actual = frappe.db.get_value("Storage Bin", self.bin_a, "check_digits")
        self.assertTrue(verify_check_digits(self.bin_a, actual.lower()))
        self.assertTrue(verify_check_digits(self.bin_a, actual.upper()))
        self.assertFalse(verify_check_digits(self.bin_a, "ZZ"))
        self.assertFalse(verify_check_digits(self.bin_a, ""))

    def test_setting_off_a_plain_bin_scan_still_works_as_before(self):
        self._set_check_digits(False)
        task = self._make_task()
        result = confirm_task(task.name, scanned_source=self.bin_a, scanned_destination=self.bin_b, confirmed_quantity=10)
        self.assertEqual(result["status"], "Confirmed")

    def test_setting_on_a_plain_bin_scan_is_rejected_check_digits_required_instead(self):
        self._set_check_digits(True)
        task = self._make_task()
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, scanned_source=self.bin_a, scanned_destination=self.bin_b, confirmed_quantity=10)

    def test_setting_on_wrong_check_digits_rejected_correct_ones_accepted(self):
        self._set_check_digits(True)
        task = self._make_task()
        src_digits = frappe.db.get_value("Storage Bin", self.bin_a, "check_digits")
        dst_digits = frappe.db.get_value("Storage Bin", self.bin_b, "check_digits")
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, scanned_source="ZZ", scanned_destination=dst_digits, confirmed_quantity=10)
        result = confirm_task(task.name, scanned_source=src_digits, scanned_destination=dst_digits, confirmed_quantity=10)
        self.assertEqual(result["status"], "Confirmed")

    def test_setting_on_a_task_with_an_hu_also_present_keeps_scanning_by_name(self):
        # Mixed bin+HU confirmation (e.g. a Pick with both source_bin and source_hu) is out of
        # scope for v1 - check digits only replace a pure-bin side, so the existing scan-by-name
        # behavior must be unaffected here.
        self._set_check_digits(True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "CHKDIGIT-PALLET",
            "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"}).insert(ignore_permissions=True)
        task = self._make_task(source_hu=hu.name)
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, scanned_source="not-the-hu", scanned_destination=frappe.db.get_value("Storage Bin", self.bin_b, "check_digits"), confirmed_quantity=10)
        result = confirm_task(task.name, scanned_source=hu.name, scanned_destination=frappe.db.get_value("Storage Bin", self.bin_b, "check_digits"), confirmed_quantity=10)
        self.assertEqual(result["status"], "Confirmed")
