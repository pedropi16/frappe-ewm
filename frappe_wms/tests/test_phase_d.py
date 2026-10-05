import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.bin_rules import bin_violations
from frappe_wms.services.determination import determine_packaging_spec
from frappe_wms.services.stock import post_entries
from frappe_wms.services.travel import bin_distance, generate_walk_path, path_distance, sort_sequence, update_order_distance


class TestPhaseD(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-PHD-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", cls.st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.area = f"{cls.wh}-AREA"
        if not frappe.db.exists("Activity Area", cls.area):
            frappe.get_doc({"doctype": "Activity Area", "warehouse": cls.wh, "area_code": "AREA", "area_name": "Area", "active": 1}).insert(ignore_permissions=True)
        cls.bins = {}
        # two aisles; coordinates in metres: A1 (1,0) A2 (1,10) A3 (1,20)   B1 (5,0) B2 (5,10) B3 (5,20)
        for code, aisle, x, y, seq in (("A1", "A", 1, 0, 3), ("A2", "A", 1, 10, 2), ("A3", "A", 1, 20, 1), ("B1", "B", 5, 0, 6), ("B2", "B", 5, 10, 5), ("B3", "B", 5, 20, 4)):
            name = cls.bins[code] = f"{cls.wh}-{code}"
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "activity_area": cls.area, "aisle": aisle, "x_coordinate": x, "y_coordinate": y, "z_coordinate": 0.5,
                    "active": 1, "sequence": seq}).insert(ignore_permissions=True)
        frappe.clear_cache()

    def test_distance_is_manhattan_from_the_coordinates_and_unknown_without_them(self):
        a1, a2, b2 = self.bins["A1"], self.bins["A2"], self.bins["B2"]
        self.assertEqual(bin_distance(a1, a2), 10)
        self.assertEqual(bin_distance(a1, b2), 14)  # 4 across + 10 along
        self.assertEqual(path_distance([a1, a2, a2, b2]), 14)
        nowhere = f"{self.wh}-NOWHERE"
        if not frappe.db.exists("Storage Bin", nowhere): frappe.get_doc({"doctype": "Storage Bin", "bin_code": nowhere, "warehouse": self.wh, "storage_type": self.st, "active": 1, "sequence": 9}).insert(ignore_permissions=True)
        self.assertIsNone(bin_distance(a1, nowhere))

    def test_walk_path_is_a_serpentine_through_the_aisles_and_an_unlisted_activity_keeps_the_bin_sequence(self):
        self.assertEqual(sort_sequence(self.bins["A1"], "Pick"), 3, "no bin sort yet: the bin's own sequence")
        self.assertEqual(generate_walk_path(self.area, "Pick"), 6)
        order = sorted(self.bins, key=lambda c: sort_sequence(self.bins[c], "Pick"))
        self.assertEqual(order, ["A1", "A2", "A3", "B3", "B2", "B1"], "up aisle A, back down aisle B")
        self.assertEqual(sort_sequence(self.bins["A1"], "Putaway"), 3, "only the Pick walk path was generated")

    def test_a_warehouse_orders_travel_distance_is_the_walk_over_its_tasks(self):
        wo = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.wh, "activity": "Internal Move", "priority": "Normal", "status": "Open", "batch_key": frappe.generate_hash(length=8)}).insert(ignore_permissions=True)
        for seq, (src, dst) in enumerate((("A1", "A3"), ("A2", "A3"), ("B2", "A3")), 1):
            frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.wh, "product": self.item, "planned_quantity": 1, "stock_uom": self.uom, "source_bin": self.bins[src], "destination_bin": self.bins[dst],
                "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "301", "priority": "Normal", "status": "Open", "warehouse_order": wo.name, "sequence": seq}).insert(ignore_permissions=True)
        update_order_distance(wo.name)
        self.assertEqual(frappe.db.get_value("Warehouse Order", wo.name, "travel_distance"), 10 + 4 + 14, "A1 -> A2 -> B2 -> A3 (the last destination)")

    def test_labor_standard_formula_adds_travel_handling_and_allowance(self):
        from frappe_wms.services.labor import planned_task_seconds
        before = frappe.db.get_value("WMS Product", {"item": self.item}, ["gross_weight_per_unit", "name"], as_dict=True)
        product = before.name if before else frappe.get_doc({"doctype": "WMS Product", "item": self.item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True).name
        old_weight = frappe.db.get_value("WMS Product", product, "gross_weight_per_unit")
        frappe.db.set_value("WMS Product", product, "gross_weight_per_unit", 2)
        self.addCleanup(lambda: frappe.db.set_value("WMS Product", product, "gross_weight_per_unit", old_weight))
        standard = frappe._dict(standard_seconds_per_unit=2, base_allowance_seconds=10, travel_seconds_per_meter=1, handling_seconds_per_kg=0.5, handling_seconds_per_volume=0, pfd_percent=10)
        task = frappe._dict(product=self.item, planned_quantity=3, source_bin=self.bins["A1"], destination_bin=self.bins["A2"])
        self.assertAlmostEqual(planned_task_seconds(standard, task), (10 + 2 * 3 + 1 * 10 + 0.5 * 6) * 1.1)

    def test_bulk_stack_height_times_lane_depth_is_the_capacity_when_no_maximum_is_set(self):
        name = f"{self.wh}-BULK"
        if not frappe.db.exists("Storage Bin", name):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": self.wh, "storage_type": self.st, "active": 1, "sequence": 8, "stack_height": 2, "lane_depth": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-PHD-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-PHD-PALLET", "hu_type_name": "PHD Pallet"}).insert(ignore_permissions=True)
        for _ in range(2):
            frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-PHD-PALLET", "warehouse": self.wh, "current_bin": name, "status": "Open"}).insert(ignore_permissions=True)
        self.assertIn("HU count capacity exceeded", bin_violations(name, destination_hu="X"), "2 high x 1 deep = 2 HUs, both places taken")
        frappe.db.set_value("Storage Bin", name, "lane_depth", 2)
        self.assertNotIn("HU count capacity exceeded", bin_violations(name, destination_hu="X"))

    def test_hu_payload_comes_from_its_type_and_posting_beyond_it_is_refused(self):
        if not frappe.db.exists("Handling Unit Type", "TEST-PHD-TOTE"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-PHD-TOTE", "hu_type_name": "PHD Tote", "maximum_payload": 10, "length": 40, "width": 30, "height": 20}).insert(ignore_permissions=True)
        product = frappe.db.get_value("WMS Product", {"item": self.item}) or frappe.get_doc({"doctype": "WMS Product", "item": self.item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True).name
        old = frappe.db.get_value("WMS Product", product, "gross_weight_per_unit")
        frappe.db.set_value("WMS Product", product, "gross_weight_per_unit", 3)
        self.addCleanup(lambda: frappe.db.set_value("WMS Product", product, "gross_weight_per_unit", old))
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-PHD-TOTE", "warehouse": self.wh, "current_bin": self.bins["A1"], "status": "Open"}).insert(ignore_permissions=True)
        self.assertEqual((hu.max_payload_weight, hu.length, hu.height), (10, 40, 20))
        entry = lambda qty, n: [{"warehouse": self.wh, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bins["A1"], "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701"}]  # noqa: E731
        post_entries(entry(3, 1), "Handling Unit", hu.name, f"test-phd-pay:{hu.name}:1")  # 9 kg
        frappe.db.savepoint("payload")
        with self.assertRaisesRegex(frappe.ValidationError, "maximum payload"):
            post_entries(entry(1, 2), "Handling Unit", hu.name, f"test-phd-pay:{hu.name}:2")  # 12 kg
        frappe.db.rollback(save_point="payload")

    def test_packaging_spec_condition_technique_prefers_customer_then_supplier_then_generic(self):
        item = "TEST-PHD-SPEC-ITEM"
        if not frappe.db.exists("Item", item):
            frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": frappe.get_all("Item Group", limit=1, pluck="name")[0], "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        customer, supplier = frappe.get_all("Customer", limit=1, pluck="name")[0], frappe.get_all("Supplier", limit=1, pluck="name")[0]
        levels = [{"level_name": "Case", "quantity_per_level": 6}]
        for extra in ({}, {"supplier": supplier}, {"customer": customer}):
            frappe.get_doc({"doctype": "Packaging Spec", "item": item, "active": 1, "levels": levels, **extra}).insert(ignore_permissions=True)
        self.assertEqual(determine_packaging_spec(item), item, "the generic spec keeps the item's name")
        self.assertEqual(determine_packaging_spec(item, supplier=supplier), f"{item}-{supplier}")
        self.assertEqual(determine_packaging_spec(item, customer=customer, supplier=supplier), f"{item}-{customer}", "the customer beats the supplier")
        self.assertEqual(determine_packaging_spec(item, customer="Nobody"), item)
