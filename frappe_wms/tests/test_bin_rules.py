import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.bin_rules import bin_violations, validate_destination_bin
from frappe_wms.services.task import create_and_confirm_move


class TestBinRules(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "BINRULES-TEST-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "hu_managed": 0, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", f"{cls.warehouse}-BIN"):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{cls.warehouse}-BIN", "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1, "maximum_hus": 2}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "BINRULES-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "BINRULES-PALLET", "hu_type_name": "Binrules Pallet"}).insert(ignore_permissions=True)
        cls.bin = f"{cls.warehouse}-BIN"

    def setUp(self):
        frappe.db.set_value("Storage Bin", self.bin, {"current_hu_count": 0, "active": 1, "putaway_blocked": 0, "maximum_weight": 0, "maximum_hus": 2})
        frappe.db.set_value("Storage Type", f"{self.warehouse}-ST", {"capacity_check_method": "HU Count", "allow_mixed_products": 0, "allow_mixed_stock_types": 0, "allow_mixed_batches": 0, "hu_managed": 0})
        for row in frappe.get_all("Storage Bin Allowed Stock Type", filters={"parent": self.bin}, pluck="name"):
            frappe.delete_doc("Storage Bin Allowed Stock Type", row, force=True)
        settings = frappe.get_single("WMS Settings")
        settings.enforce_storage_type_rules = 0
        settings.save(ignore_permissions=True)
        frappe.clear_cache()

    def test_inactive_bin_is_rejected(self):
        frappe.db.set_value("Storage Bin", self.bin, "active", 0)
        reasons = bin_violations(self.bin)
        self.assertTrue(reasons)
        frappe.db.set_value("Storage Bin", self.bin, "active", 1)

    def test_stock_type_whitelist_excludes_non_whitelisted_type(self):
        bin_doc = frappe.get_doc("Storage Bin", self.bin)
        bin_doc.append("allowed_stock_types", {"stock_type": "AVAILABLE"})
        bin_doc.save(ignore_permissions=True)
        self.assertEqual(bin_violations(self.bin, stock_type="AVAILABLE"), [])
        self.assertTrue(bin_violations(self.bin, stock_type="QUALITY"))

    def test_hu_count_capacity_is_enforced(self):
        frappe.db.set_value("Storage Bin", self.bin, "current_hu_count", 2)
        self.assertTrue(bin_violations(self.bin, incoming_hu_count=1))
        frappe.db.set_value("Storage Bin", self.bin, "current_hu_count", 1)
        self.assertEqual(bin_violations(self.bin, incoming_hu_count=1), [])

    def test_weight_capacity_is_enforced(self):
        frappe.db.set_value("Storage Type", f"{self.warehouse}-ST", "capacity_check_method", "Weight")
        frappe.db.set_value("Storage Bin", self.bin, {"maximum_weight": 100, "current_weight": 90})
        self.assertTrue(bin_violations(self.bin, incoming_weight=20))
        self.assertEqual(bin_violations(self.bin, incoming_weight=5), [])

    def test_hu_managed_storage_type_requires_a_destination_hu(self):
        frappe.db.set_value("Storage Type", f"{self.warehouse}-ST", "hu_managed", 1)
        self.assertTrue(bin_violations(self.bin, destination_hu=None))
        self.assertEqual(bin_violations(self.bin, destination_hu="SOME-HU"), [])

    def test_mixing_rules_only_enforced_when_setting_enabled(self):
        post_entries = __import__("frappe_wms.services.stock", fromlist=["post_entries"]).post_entries
        post_entries(
            [{"warehouse": self.warehouse, "product": self.item, "storage_bin": self.bin,
              "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701"}],
            "Storage Bin", self.bin, f"test-bin-rules:{frappe.generate_hash(length=8)}",
        )
        other_item = "TEST-BINRULES-ITEM-2"
        if not frappe.db.exists("Item", other_item):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": other_item, "item_name": other_item, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)

        # Off by default: a different product in the same bin is not flagged.
        self.assertEqual(bin_violations(self.bin, item=other_item), [])

        settings = frappe.get_single("WMS Settings")
        settings.enforce_storage_type_rules = 1
        settings.save(ignore_permissions=True)
        frappe.clear_cache()
        try:
            self.assertTrue(bin_violations(self.bin, item=other_item))
            self.assertEqual(bin_violations(self.bin, item=self.item), [])
        finally:
            settings.enforce_storage_type_rules = 0
            settings.save(ignore_permissions=True)
            frappe.clear_cache()

    def test_validate_destination_bin_throws_with_reasons(self):
        frappe.db.set_value("Storage Bin", self.bin, "active", 0)
        with self.assertRaises(frappe.ValidationError):
            validate_destination_bin(self.bin)
        frappe.db.set_value("Storage Bin", self.bin, "active", 1)

    def test_create_and_confirm_move_rejects_a_blocked_destination_bin(self):
        if not frappe.db.exists("WMS Product", {"item": self.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": self.item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "BINRULES-PALLET", "warehouse": self.warehouse, "current_bin": self.bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        from frappe_wms.services.stock import post_entries
        post_entries(
            [{"warehouse": self.warehouse, "product": self.item, "storage_bin": self.bin, "handling_unit": hu.name,
              "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 3, "movement_type": "701"}],
            "Storage Bin", self.bin, f"test-bin-rules-move:{frappe.generate_hash(length=8)}",
        )
        frappe.db.set_value("Storage Bin", self.bin, "putaway_blocked", 1)
        try:
            with self.assertRaises(frappe.ValidationError):
                create_and_confirm_move(warehouse=self.warehouse, product=self.item, quantity=1, stock_uom=self.uom,
                    stock_type="AVAILABLE", source_bin=self.bin, source_hu=hu.name, destination_bin=self.bin)
        finally:
            frappe.db.set_value("Storage Bin", self.bin, "putaway_blocked", 0)
