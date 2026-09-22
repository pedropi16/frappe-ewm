import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.monitor import resource_performance
from frappe_wms.services.warehouse_order import attach_task


class TestLaborManagement(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "LABOR-TEST-WH"
        cls.bin_a = f"{cls.warehouse}-A"
        cls.bin_b = f"{cls.warehouse}-B"
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
        if not frappe.db.exists("Warehouse Queue", "LABOR-TEST-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "LABOR-TEST-QUEUE", "queue_name": "Labor Test Queue", "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Resource", "LABOR-TEST-RESOURCE"):
            frappe.get_doc({"doctype": "WMS Resource", "resource_code": "LABOR-TEST-RESOURCE", "warehouse": cls.warehouse,
                "resource_type": "Operator", "current_queue": "LABOR-TEST-QUEUE", "active": 1}).insert(ignore_permissions=True)

    def tearDown(self):
        for existing in frappe.get_all("Labor Standard", filters={"warehouse": self.warehouse}, pluck="name"):
            frappe.delete_doc("Labor Standard", existing, force=True, ignore_permissions=True)

    def _make_and_confirm_task(self, planned_quantity=5):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": planned_quantity, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        attach_task(task, frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        self.assertEqual(task.assigned_resource, "LABOR-TEST-RESOURCE", "queue-logged-on resource must be auto-assigned")
        confirm_task(task.name, confirmed_quantity=planned_quantity)
        return task

    def test_resource_with_matching_standard_gets_efficiency(self):
        frappe.get_doc({"doctype": "Labor Standard", "priority": 1, "warehouse": self.warehouse,
            "task_type": "Internal Move", "standard_seconds_per_unit": 30, "active": 1}).insert(ignore_permissions=True)
        self._make_and_confirm_task()

        rows = resource_performance(self.warehouse)
        matching = [r for r in rows if r["assigned_resource"] == "LABOR-TEST-RESOURCE"]
        self.assertEqual(len(matching), 1)
        self.assertGreaterEqual(matching[0]["task_count"], 1)
        self.assertIsNotNone(matching[0]["avg_task_cycle_time_hours"])
        self.assertIsNotNone(matching[0]["efficiency_percent"])
        self.assertGreater(matching[0]["efficiency_percent"], 0)

    def test_resource_with_no_matching_standard_reports_no_efficiency(self):
        self._make_and_confirm_task()
        rows = resource_performance(self.warehouse)
        matching = [r for r in rows if r["assigned_resource"] == "LABOR-TEST-RESOURCE"]
        self.assertEqual(len(matching), 1)
        self.assertGreaterEqual(matching[0]["task_count"], 1)
        self.assertIsNotNone(matching[0]["avg_task_cycle_time_hours"])
        self.assertIsNone(matching[0]["efficiency_percent"])

    def test_warehouse_kpis_unaffected_by_labor_standards(self):
        from frappe_wms.api.monitor import warehouse_kpis
        frappe.get_doc({"doctype": "Labor Standard", "priority": 1, "warehouse": self.warehouse,
            "task_type": "Internal Move", "standard_seconds_per_unit": 30, "active": 1}).insert(ignore_permissions=True)
        self._make_and_confirm_task()
        result = warehouse_kpis(self.warehouse)
        self.assertGreaterEqual(result["task_throughput_total"], 1)
