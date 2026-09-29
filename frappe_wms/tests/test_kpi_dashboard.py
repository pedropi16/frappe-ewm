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

    def test_get_alerts_surfaces_aged_open_task_but_not_a_fresh_one(self):
        fresh = self._make_task()
        aged = self._make_task()
        frappe.db.set_value("Warehouse Task", aged.name, "modified", add_to_date(now_datetime(), hours=-5), update_modified=False)

        alerts = get_alerts(self.warehouse)
        open_names = [r.name for r in alerts["aged_open_tasks"]]
        self.assertIn(aged.name, open_names)
        self.assertNotIn(fresh.name, open_names)

    def test_get_alerts_surfaces_stock_sitting_in_an_interim_bin_past_the_alert_age(self):
        from frappe_wms.services.stock import post_entries
        staging_type = f"{self.warehouse}-STAGE-ALERT"
        staging_bin = f"{self.warehouse}-STAGE-ALERT-BIN"
        if not frappe.db.exists("Storage Type", staging_type):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": self.warehouse, "storage_type_code": "STAGE-ALERT", "storage_type_name": "Stage Alert",
                "storage_role": "Staging", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", staging_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": staging_bin, "warehouse": self.warehouse, "storage_type": staging_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        item_code = "TEST-KPI-ALERT-INTERIM"
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        post_entries([{
            "warehouse": self.warehouse, "product": item_code, "storage_bin": staging_bin,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 7, "movement_type": "701",
        }], "Storage Bin", staging_bin, f"test-kpi-alert-interim:{frappe.generate_hash(length=8)}")
        balance_name = frappe.db.get_value("WMS Stock Balance", {"storage_bin": staging_bin, "product": item_code}, "name")
        frappe.db.set_value("WMS Stock Balance", balance_name, "last_movement_date", add_to_date(now_datetime(), hours=-5))

        alerts = get_alerts(self.warehouse)
        self.assertIn(balance_name, [r.name for r in alerts["stock_in_interim_bins"]])
        # A normal Storage-role bin's stock, however old, is not an interim-bin alert.
        self.assertNotIn(balance_name, [r.name for r in alerts.get("negative_quants", [])])

    def test_get_alerts_surfaces_negative_quants(self):
        from frappe_wms.services.stock import post_entries
        item_code = "TEST-KPI-ALERT-NEGATIVE"
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        # self.warehouse has allow_negative_stock=1 (see setUpClass) - a straight negative entry
        # against a bin with no existing balance is this app's own documented way to produce one.
        post_entries([{
            "warehouse": self.warehouse, "product": item_code, "storage_bin": self.bin_a,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": -3, "movement_type": "702",
        }], "Storage Bin", self.bin_a, f"test-kpi-alert-negative:{frappe.generate_hash(length=8)}")

        alerts = get_alerts(self.warehouse)
        negative_products = [r.product for r in alerts["negative_quants"]]
        self.assertIn(item_code, negative_products)

    def test_get_alerts_surfaces_open_task_differences(self):
        from frappe_wms.services.stock import post_entries
        diff_bin = f"{self.warehouse}-DIFFBIN-ALERT"
        if not frappe.db.exists("Storage Bin", diff_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": diff_bin, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", self.warehouse)
        if not wh.default_difference_bin:
            wh.default_difference_bin = diff_bin
            wh.save(ignore_permissions=True)
        item_code = "TEST-KPI-ALERT-DIFF"
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        post_entries([{
            "warehouse": self.warehouse, "product": item_code, "storage_bin": self.bin_a,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 20, "movement_type": "701",
        }], "Storage Bin", self.bin_a, f"test-kpi-alert-diff-seed:{frappe.generate_hash(length=8)}")
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse, "product": item_code,
            "planned_quantity": 10, "stock_uom": self.uom, "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        task.insert(ignore_permissions=True)
        result = confirm_task(task.name, confirmed_quantity=13)

        alerts = get_alerts(self.warehouse)
        self.assertIn(result["difference"], [r.name for r in alerts["open_differences"]])
