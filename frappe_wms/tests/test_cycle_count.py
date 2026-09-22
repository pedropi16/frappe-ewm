import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from frappe_wms.services.stock import post_entries
from frappe_wms.services.cycle_count import generate_scheduled_counts


class TestCycleCount(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "CCR-TEST-WH"
        cls.bin_a = f"{cls.warehouse}-A"
        cls.bin_b = f"{cls.warehouse}-B"
        cls.uom = "Nos"
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.bin_a, cls.bin_b):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

    def tearDown(self):
        for existing in frappe.get_all("Cycle Count Rule", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("Cycle Count Rule", existing, force=True, ignore_permissions=True)
        for existing in frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("WMS Physical Inventory Count", existing, force=True, ignore_permissions=True)

    def _make_item(self, item_code, abc_indicator=None):
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": self.item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "abc_indicator": abc_indicator, "active": 1}).insert(ignore_permissions=True)
        elif abc_indicator:
            frappe.db.set_value("WMS Product", {"item": item_code}, "abc_indicator", abc_indicator)
        return item_code

    def _seed_stock(self, item, storage_bin, qty):
        post_entries([{
            "warehouse": self.warehouse, "product": item, "storage_bin": storage_bin,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701",
        }], "Storage Bin", storage_bin, f"test-ccr-seed:{frappe.generate_hash(length=8)}")

    def _rule(self, **kwargs):
        payload = {"doctype": "Cycle Count Rule", "priority": 1, "warehouse": self.warehouse, "frequency_days": 30, "active": 1}
        payload.update(kwargs)
        return frappe.get_doc(payload).insert(ignore_permissions=True)

    def test_abc_cycle_count_generates_product_scoped_count(self):
        item = self._make_item("TEST-CCR-ABC", abc_indicator="A")
        self._rule(procedure_type="ABC Cycle Count", abc_indicator="A")
        created = generate_scheduled_counts()
        matching = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse, "product": item}, pluck="name")
        self.assertEqual(len(matching), 1)
        self.assertIn(matching[0], created)

    def test_abc_cycle_count_skips_recently_covered_product(self):
        item = self._make_item("TEST-CCR-ABC2", abc_indicator="A")
        frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse, "product": item, "status": "Draft"}).insert(ignore_permissions=True)
        self._rule(procedure_type="ABC Cycle Count", abc_indicator="A")
        generate_scheduled_counts()
        matching = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse, "product": item}, pluck="name")
        self.assertEqual(len(matching), 1, "an existing count within the frequency window must not be duplicated")

    def test_low_stock_generates_bin_scoped_count(self):
        item = self._make_item("TEST-CCR-LOW")
        self._seed_stock(item, self.bin_a, 2)
        frappe.get_doc({"doctype": "Replenishment Rule", "warehouse": self.warehouse, "product": item, "storage_bin": self.bin_a,
            "stock_type": "AVAILABLE", "minimum_quantity": 5, "target_quantity": 20, "source_storage_type": f"{self.warehouse}-ST",
            "priority": "Normal", "active": 1}).insert(ignore_permissions=True)
        self._rule(procedure_type="Low Stock")
        generate_scheduled_counts()
        matching = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse, "storage_bin": self.bin_a}, pluck="name")
        self.assertEqual(len(matching), 1)

    def test_zero_stock_generates_bin_scoped_count(self):
        item = self._make_item("TEST-CCR-ZERO")
        self._seed_stock(item, self.bin_a, 5)
        post_entries([{
            "warehouse": self.warehouse, "product": item, "storage_bin": self.bin_a,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": -5, "movement_type": "702",
        }], "Storage Bin", self.bin_a, f"test-ccr-zero-out:{frappe.generate_hash(length=8)}")
        self._rule(procedure_type="Zero Stock")
        generate_scheduled_counts()
        matching = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse, "storage_bin": self.bin_a}, pluck="name")
        self.assertEqual(len(matching), 1)

    def test_putaway_pi_generates_bin_scoped_count(self):
        item = self._make_item("TEST-CCR-PUTAWAY")
        post_entries([{
            "warehouse": self.warehouse, "product": item, "storage_bin": self.bin_b,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 10, "movement_type": "201", "reference_line": None,
        }], "Storage Bin", self.bin_b, f"test-ccr-putaway:{frappe.generate_hash(length=8)}")
        self._rule(procedure_type="Putaway PI")
        generate_scheduled_counts()
        matching = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse, "storage_bin": self.bin_b}, pluck="name")
        self.assertEqual(len(matching), 1)

    def test_bin_check_generates_a_count_per_active_bin_in_storage_type(self):
        self._rule(procedure_type="Bin Check", storage_type=f"{self.warehouse}-ST")
        generate_scheduled_counts()
        matching = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse}, pluck="storage_bin")
        self.assertIn(self.bin_a, matching)
        self.assertIn(self.bin_b, matching)

    def test_annual_generates_one_warehouse_wide_count(self):
        self._rule(procedure_type="Annual", frequency_days=365)
        generate_scheduled_counts()
        matching = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse, "product": ["in", ["", None]], "storage_bin": ["in", ["", None]]}, pluck="name")
        self.assertEqual(len(matching), 1)
        generate_scheduled_counts()
        matching_again = frappe.get_all("WMS Physical Inventory Count", filters={"warehouse": self.warehouse, "product": ["in", ["", None]], "storage_bin": ["in", ["", None]]}, pluck="name")
        self.assertEqual(len(matching_again), 1, "a second run within the frequency window must not create a duplicate")
