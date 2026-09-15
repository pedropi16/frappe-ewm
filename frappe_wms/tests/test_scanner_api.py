import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import list_exception_codes, my_tasks, raise_exception


class TestScannerApi(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "SCANNER-TEST-WH"
        cls.bin_a = f"{cls.warehouse}-A"
        cls.bin_b = f"{cls.warehouse}-B"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.bin_a, cls.bin_b):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

    def _make_task(self, **overrides):
        payload = {
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 5, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        }
        payload.update(overrides)
        task = frappe.get_doc(payload)
        task.insert(ignore_permissions=True)
        return task

    def test_my_tasks_lists_open_tasks_in_permitted_warehouse(self):
        task = self._make_task()
        result = my_tasks()
        names = [t["name"] for t in result["tasks"]]
        self.assertIn(task.name, names)

    def test_raise_exception_blocks_task_and_prevents_confirmation(self):
        task = self._make_task()
        code = frappe.get_all("WMS Exception Code", filters={"active": 1}, limit=1, pluck="name")
        if not code:
            self.skipTest("no WMS Exception Code configured")
        code = code[0]
        result = raise_exception(task.name, code, remarks="test block")
        self.assertEqual(result["status"], "Exception")
        task.reload()
        self.assertEqual(task.status, "Exception")
        self.assertEqual(task.exception_code, code)
        self.assertEqual(task.blocking_reason, "test block")

        from frappe_wms.api.scanner import confirm_task
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, confirmed_quantity=5)

    def test_list_exception_codes_returns_active_codes(self):
        codes = list_exception_codes()
        self.assertTrue(all(isinstance(c, dict) and "name" in c for c in codes))
