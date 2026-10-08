import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, nowdate, now_datetime

from frappe_wms.services import labor


class TestLaborShifts(IntegrationTestCase):
    def test_shift_length_and_indirect_labor(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        wh = f"LAB-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Labor Shift", "warehouse": wh, "shift_name": "Night", "start_time": "22:00:00", "end_time": "06:00:00"}).insert(ignore_permissions=True)
        self.assertEqual(labor.shift_hours(wh), 8, "a shift past midnight")
        res = frappe.get_doc({"doctype": "WMS Resource", "resource_code": f"R-{wh}", "warehouse": wh, "resource_type": "Operator", "active": 1}).insert(ignore_permissions=True).name
        entry = labor.start_indirect(res, "Cleaning")
        frappe.db.set_value("Indirect Labor Entry", entry, "started_at", add_to_date(now_datetime(), hours=-2))
        labor.start_indirect(res, "Break")  # closes the open cleaning entry
        self.assertAlmostEqual(frappe.db.get_value("Indirect Labor Entry", entry, "minutes"), 120, delta=1)
        labor.stop_indirect(res)
        [row] = labor.labor_summary(wh, nowdate(), nowdate())
        self.assertEqual((row["resource"], round(row["indirect_hours"]["Cleaning"])), (res, 2))
        self.assertEqual(row["utilization_percent"], round(row["indirect_hours"]["Cleaning"] / 8 * 100 + row["indirect_hours"].get("Break", 0) / 8 * 100, 1))
