import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from frappe_wms.api.scanner import confirm_task, raise_exception
from frappe_wms.api.monitor import warehouse_kpis, get_alerts
from frappe_wms.services.warehouse_order import attach_task


class TestKPIDashboard(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "KPI-TEST-WH"
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
        if not frappe.db.exists("WMS Exception Code", "KPI-TEST-EXC"):
            frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": "KPI-TEST-EXC", "exception_name": "KPI Test Exception", "category": "Task", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "KPI-TEST-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "KPI-TEST-QUEUE", "queue_name": "KPI Test Queue", "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)

    def _make_task(self, batch_key=None):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 5, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        attach_task(task, batch_key or frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        return task

    def test_task_throughput_and_exception_rate(self):
        confirmed = self._make_task()
        confirm_task(confirmed.name, confirmed_quantity=5)

        excepted = self._make_task()
        raise_exception(excepted.name, "KPI-TEST-EXC", remarks="test")

        result = warehouse_kpis(self.warehouse)
        self.assertGreaterEqual(result["task_throughput_total"], 1)
        self.assertIsNotNone(result["avg_task_cycle_time_hours"])
        self.assertIsNotNone(result["exception_rate_percent"])
        self.assertGreater(result["exception_rate_percent"], 0)

    def test_warehouse_order_cycle_time_is_populated_once_completed(self):
        batch_key = frappe.generate_hash(length=10)
        task = self._make_task(batch_key)
        confirm_task(task.name, confirmed_quantity=5)
        self.assertTrue(task.warehouse_order)
        result = warehouse_kpis(self.warehouse)
        # Not asserting a specific value (a same-request completion can be ~0 seconds) -
        # just that a Completed Warehouse Order produces a real (non-None) number.
        self.assertIsNotNone(result["avg_wo_cycle_time_hours"])

    def test_get_alerts_surfaces_aged_exception_but_not_a_fresh_one(self):
        fresh = self._make_task()
        raise_exception(fresh.name, "KPI-TEST-EXC", remarks="fresh")

        aged = self._make_task()
        raise_exception(aged.name, "KPI-TEST-EXC", remarks="aged")
        frappe.db.set_value("Warehouse Task", aged.name, "modified", add_to_date(now_datetime(), hours=-5), update_modified=False)

        alerts = get_alerts(self.warehouse)
        exception_names = [r.name for r in alerts["aged_exceptions"]]
        self.assertIn(aged.name, exception_names)
        self.assertNotIn(fresh.name, exception_names)

    def test_get_alerts_surfaces_under_review_count(self):
        item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        if not frappe.db.exists("Count Tolerance Group", {"warehouse": self.warehouse}):
            frappe.get_doc({"doctype": "Count Tolerance Group", "priority": 1, "warehouse": self.warehouse, "item_group": item_group,
                "tolerance_percentage": 1, "tolerance_quantity": 0, "requires_recount": 1, "requires_approval": 1, "active": 1}).insert(ignore_permissions=True)

        from frappe_wms.services.stock import post_entries
        from frappe_wms.services.inventory_count import snapshot_count, record_counts, post_count
        item_code = "TEST-KPI-ALERT-ITEM"
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        post_entries([{
            "warehouse": self.warehouse, "product": item_code, "storage_bin": self.bin_a,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 100, "movement_type": "701",
        }], "Storage Bin", self.bin_a, f"test-kpi-alert-seed:{frappe.generate_hash(length=8)}")
        count = frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse,
            "storage_bin": self.bin_a, "product": item_code, "status": "Draft"}).insert(ignore_permissions=True)
        snapshot_count(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: 50})  # huge variance, out of tolerance
        post_count(count.name)
        count.reload()
        self.assertEqual(count.status, "Under Review")

        alerts = get_alerts(self.warehouse)
        self.assertIn(count.name, [r.name for r in alerts["pending_approval_counts"]])
