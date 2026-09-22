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
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
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
