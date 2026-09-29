import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.warehouse_order import attach_task


class TestWOCreationRule(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-WOCR-WH"
        cls.bin_a = "WMS-TEST-WOCR-WH-A"
        cls.bin_b = "WMS-TEST-WOCR-WH-B"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.bin_a, cls.bin_b):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "WMS-TEST-WOCR-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "WMS-TEST-WOCR-QUEUE", "queue_name": "Test WOCR Queue", "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "WMS-TEST-WOCR-PICK-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "WMS-TEST-WOCR-PICK-QUEUE", "queue_name": "Test WOCR Pick Queue", "warehouse": cls.warehouse, "activity": "Pick", "active": 1}).insert(ignore_permissions=True)

    def tearDown(self):
        for existing in frappe.get_all("WO Creation Rule", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("WO Creation Rule", existing, force=True, ignore_permissions=True)

    def _make_task(self, batch_key, sequence=None):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open", "sequence": sequence,
        })
        attach_task(task, batch_key)
        task.insert(ignore_permissions=True)
        return task

    def test_tasks_spill_into_a_new_warehouse_order_once_the_rule_cap_is_hit(self):
        frappe.get_doc({"doctype": "WO Creation Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Internal Move", "maximum_tasks": 2, "active": 1}).insert(ignore_permissions=True)

        batch_key = frappe.generate_hash(length=10)
        first = self._make_task(batch_key, sequence=1)
        second = self._make_task(batch_key, sequence=2)
        third = self._make_task(batch_key, sequence=3)

        self.assertEqual(first.warehouse_order, second.warehouse_order)
        self.assertNotEqual(second.warehouse_order, third.warehouse_order)
        self.assertEqual(frappe.db.get_value("Warehouse Order", first.warehouse_order, "task_count"), 2)
        self.assertEqual(frappe.db.get_value("Warehouse Order", third.warehouse_order, "task_count"), 1)

    def test_without_a_rule_all_tasks_in_the_batch_share_one_warehouse_order(self):
        batch_key = frappe.generate_hash(length=10)
        first = self._make_task(batch_key, sequence=1)
        second = self._make_task(batch_key, sequence=2)
        third = self._make_task(batch_key, sequence=3)

        self.assertEqual(first.warehouse_order, second.warehouse_order)
        self.assertEqual(second.warehouse_order, third.warehouse_order)
        self.assertEqual(frappe.db.get_value("Warehouse Order", first.warehouse_order, "task_count"), 3)

    def _make_weighted_item(self, item_code, gross_weight_per_unit=0, volume_per_unit=0):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1,
                "gross_weight_per_unit": gross_weight_per_unit, "volume_per_unit": volume_per_unit}).insert(ignore_permissions=True)
        return item_code

    def _make_task_for(self, item_code, batch_key, sequence=None, task_type="Internal Move", destination_bin=None):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": task_type, "warehouse": self.warehouse,
            "product": item_code, "planned_quantity": 1, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": destination_bin or self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open", "sequence": sequence,
        })
        attach_task(task, batch_key)
        task.insert(ignore_permissions=True)
        return task

    def test_tasks_spill_into_a_new_warehouse_order_once_the_weight_cap_would_be_exceeded(self):
        item_code = self._make_weighted_item("TEST-WOCR-WEIGHT", gross_weight_per_unit=5)
        frappe.get_doc({"doctype": "WO Creation Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Internal Move", "maximum_weight": 12, "active": 1}).insert(ignore_permissions=True)

        batch_key = frappe.generate_hash(length=10)
        first = self._make_task_for(item_code, batch_key, sequence=1)
        second = self._make_task_for(item_code, batch_key, sequence=2)
        third = self._make_task_for(item_code, batch_key, sequence=3)

        self.assertEqual(first.warehouse_order, second.warehouse_order)
        self.assertNotEqual(second.warehouse_order, third.warehouse_order)
        self.assertEqual(frappe.db.get_value("Warehouse Order", first.warehouse_order, "total_weight"), 10)
        self.assertEqual(frappe.db.get_value("Warehouse Order", third.warehouse_order, "total_weight"), 5)

    def test_tasks_spill_into_a_new_warehouse_order_once_the_volume_cap_would_be_exceeded(self):
        item_code = self._make_weighted_item("TEST-WOCR-VOLUME", volume_per_unit=3)
        frappe.get_doc({"doctype": "WO Creation Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Internal Move", "maximum_volume": 7, "active": 1}).insert(ignore_permissions=True)

        batch_key = frappe.generate_hash(length=10)
        first = self._make_task_for(item_code, batch_key, sequence=1)
        second = self._make_task_for(item_code, batch_key, sequence=2)
        third = self._make_task_for(item_code, batch_key, sequence=3)

        self.assertEqual(first.warehouse_order, second.warehouse_order)
        self.assertNotEqual(second.warehouse_order, third.warehouse_order)
        self.assertEqual(frappe.db.get_value("Warehouse Order", first.warehouse_order, "total_volume"), 6)

    def test_tasks_spill_into_a_new_warehouse_order_once_the_estimated_minutes_cap_would_be_exceeded(self):
        item_code = self._make_weighted_item("TEST-WOCR-MINUTES")
        frappe.get_doc({"doctype": "WO Creation Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Internal Move", "standard_minutes_per_task": 4, "maximum_minutes": 10, "active": 1}).insert(ignore_permissions=True)

        batch_key = frappe.generate_hash(length=10)
        first = self._make_task_for(item_code, batch_key, sequence=1)
        second = self._make_task_for(item_code, batch_key, sequence=2)
        third = self._make_task_for(item_code, batch_key, sequence=3)

        self.assertEqual(first.warehouse_order, second.warehouse_order)
        self.assertNotEqual(second.warehouse_order, third.warehouse_order)
        self.assertEqual(frappe.db.get_value("Warehouse Order", first.warehouse_order, "estimated_minutes"), 8)

    def test_pick_hu_type_auto_creates_one_shared_destination_hu_per_warehouse_order(self):
        item_code = self._make_weighted_item("TEST-WOCR-PICKHU")
        if not frappe.db.exists("Handling Unit Type", "WOCR-PICK-CART"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WOCR-PICK-CART", "hu_type_name": "WOCR Pick Cart"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WO Creation Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Pick", "pick_hu_type": "WOCR-PICK-CART", "active": 1}).insert(ignore_permissions=True)

        batch_key = frappe.generate_hash(length=10)
        first = self._make_task_for(item_code, batch_key, sequence=1, task_type="Pick")
        second = self._make_task_for(item_code, batch_key, sequence=2, task_type="Pick")

        self.assertEqual(first.warehouse_order, second.warehouse_order)
        self.assertTrue(first.destination_hu)
        self.assertEqual(first.destination_hu, second.destination_hu)
        self.assertEqual(frappe.db.get_value("Warehouse Order", first.warehouse_order, "pick_handling_unit"), first.destination_hu)
        self.assertEqual(frappe.db.get_value("Handling Unit", first.destination_hu, "hu_type"), "WOCR-PICK-CART")

    def test_pick_hu_type_does_not_override_an_explicit_destination_hu(self):
        item_code = self._make_weighted_item("TEST-WOCR-PICKHU-EXPLICIT")
        if not frappe.db.exists("Handling Unit Type", "WOCR-EXPLICIT-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WOCR-EXPLICIT-PALLET", "hu_type_name": "WOCR Explicit Pallet"}).insert(ignore_permissions=True)
        explicit_hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10),
            "hu_type": "WOCR-EXPLICIT-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_b, "status": "Open"})
        explicit_hu.insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "WOCR-PICK-CART-2"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WOCR-PICK-CART-2", "hu_type_name": "WOCR Pick Cart 2"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WO Creation Rule", "priority": 1, "warehouse": self.warehouse,
            "activity": "Pick", "pick_hu_type": "WOCR-PICK-CART-2", "active": 1}).insert(ignore_permissions=True)

        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Pick", "warehouse": self.warehouse,
            "product": item_code, "planned_quantity": 1, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b, "destination_hu": explicit_hu.name,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open", "sequence": 1,
        })
        attach_task(task, frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)

        self.assertEqual(task.destination_hu, explicit_hu.name)
