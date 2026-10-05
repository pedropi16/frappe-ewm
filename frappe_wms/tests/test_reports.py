import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

from frappe_wms.services.stock import post_entries
from frappe_wms.wms_core.report.wms_bin_utilization.wms_bin_utilization import execute as bin_utilization
from frappe_wms.wms_core.report.wms_task_analysis.wms_task_analysis import execute as task_analysis


class TestReports(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-RPT-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", cls.st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "None", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = []
        for n in (1, 2, 3, 4):
            name = f"{cls.wh}-B{n}"
            cls.bins.append(name)
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "active": 1, "sequence": n, "putaway_blocked": 1 if n == 4 else 0}).insert(ignore_permissions=True)

    def test_task_analysis_groups_confirmed_tasks_with_counts_quantity_and_duration(self):
        now = now_datetime()
        for qty, minutes, resource in ((5, 10, None), (3, 20, None)):
            task = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.wh, "product": self.item, "planned_quantity": qty, "stock_uom": self.uom,
                "source_bin": self.bins[0], "destination_bin": self.bins[1], "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "301", "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
            frappe.db.set_value("Warehouse Task", task.name, {"status": "Confirmed", "confirmed_quantity": qty, "started_at": add_to_date(now, minutes=-minutes), "confirmed_at": now})
        columns, data, _msg, chart = task_analysis({"warehouse": self.wh, "from_date": nowdate(), "to_date": nowdate(), "group_by": "Task Type"})
        self.assertEqual([(d["grp"], d["tasks"], d["quantity"], d["avg_minutes"]) for d in data], [("Internal Move", 2, 8.0, 15.0)])
        self.assertEqual(chart["data"]["labels"], ["Internal Move"])
        self.assertEqual(task_analysis({"warehouse": self.wh, "task_type": "Pick", "from_date": nowdate()})[1], [])

    def test_bin_utilization_counts_occupied_empty_and_blocked_bins(self):
        post_entries([{"warehouse": self.wh, "product": self.item, "storage_bin": self.bins[0], "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 4, "movement_type": "701"}], "Storage Bin", self.bins[0], f"test-rpt:{frappe.generate_hash(length=6)}")
        _cols, data, _msg, _chart = bin_utilization({"warehouse": self.wh})
        row = next(d for d in data if d["storage_type"] == self.st)
        self.assertEqual((row["bins"], row["occupied"], row["empty"], row["blocked"], row["occupancy"]), (4, 1, 3, 1, 25.0))
