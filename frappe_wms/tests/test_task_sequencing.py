import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task, raise_exception
from frappe_wms.services.warehouse_order import attach_task


class TestTaskSequencing(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-SEQ-WH"
        cls.bin_a = "WMS-TEST-SEQ-WH-A"
        cls.bin_b = "WMS-TEST-SEQ-WH-B"
        cls.bin_c = "WMS-TEST-SEQ-WH-C"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, seq in ((cls.bin_a, 1), (cls.bin_b, 2), (cls.bin_c, 3)):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": seq}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "WMS-TEST-SEQ-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "WMS-TEST-SEQ-QUEUE", "queue_name": "Test Sequencing Queue", "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)

    def _attach_and_insert(self, batch_key, source_bin, destination_bin, sequence=None):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 10, "stock_uom": self.uom,
            "source_bin": source_bin, "destination_bin": destination_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open", "sequence": sequence,
        })
        attach_task(task, batch_key)
        task.insert(ignore_permissions=True)
        return task

    def test_second_task_in_batch_starts_on_hold_and_releases_on_confirm(self):
        batch_key = frappe.generate_hash(length=10)
        first = self._attach_and_insert(batch_key, self.bin_a, self.bin_b, sequence=1)
        second = self._attach_and_insert(batch_key, self.bin_b, self.bin_c, sequence=2)

        self.assertNotEqual(first.status, "On Hold")
        second.reload()
        self.assertEqual(second.status, "On Hold")
        self.assertTrue(second.blocking_reason and first.name in second.blocking_reason)

        with self.assertRaises(frappe.ValidationError):
            confirm_task(second.name, confirmed_quantity=10)

        result = confirm_task(first.name, confirmed_quantity=10)
        self.assertEqual(result["status"], "Confirmed")
        self.assertIn(second.name, result["released_tasks"])

        second.reload()
        self.assertNotEqual(second.status, "On Hold")
        self.assertIn(second.status, ("Open", "Assigned"))

    def test_exception_on_first_task_does_not_release_second(self):
        batch_key = frappe.generate_hash(length=10)
        first = self._attach_and_insert(batch_key, self.bin_a, self.bin_b, sequence=1)
        second = self._attach_and_insert(batch_key, self.bin_b, self.bin_c, sequence=2)

        code_name = "WMS-TEST-SEQ-EXC"
        if not frappe.db.exists("WMS Exception Code", code_name):
            frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": code_name, "exception_name": code_name, "category": "Task", "follow_up_action": "Cancel Task", "active": 1}).insert(ignore_permissions=True)

        raise_exception(first.name, code_name)
        second.reload()
        self.assertEqual(second.status, "On Hold")
