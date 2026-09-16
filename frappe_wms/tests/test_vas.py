import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.vas import list_open_vas_orders, get_vas_order, complete_activity


class TestVAS(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-VAS-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-VAS-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-VAS-PALLET", "hu_type_name": "Test VAS Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.bin_a = f"{cls.warehouse}-A"
        if not frappe.db.exists("Storage Bin", cls.bin_a):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin_a, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

    def _make_vas_order(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-VAS-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"})
        hu.insert(ignore_permissions=True)
        order = frappe.get_doc({
            "doctype": "VAS Order", "handling_unit": hu.name, "warehouse": self.warehouse, "work_center_bin": self.bin_a,
            "activities": [
                {"step_no": 1, "activity_type": "Label", "instruction": "Apply compliance label"},
                {"step_no": 2, "activity_type": "Kit", "instruction": "Insert accessory kit"},
            ],
        })
        order.insert(ignore_permissions=True)
        return order

    def test_completing_all_activities_completes_the_order(self):
        order = self._make_vas_order()
        first_row, second_row = order.activities

        result = complete_activity(order.name, first_row.name)
        self.assertEqual(result["status"], "In Process")

        result = complete_activity(order.name, second_row.name)
        self.assertEqual(result["status"], "Completed")

        order.reload()
        self.assertEqual(order.status, "Completed")
        self.assertTrue(order.completed_at)
        self.assertTrue(all(r.completed for r in order.activities))

    def test_list_open_vas_orders_is_warehouse_scoped_and_reports_progress(self):
        order = self._make_vas_order()
        orders = list_open_vas_orders()
        match = next(o for o in orders if o.name == order.name)
        self.assertEqual(match.activity_count, 2)
        self.assertEqual(match.completed_count, 0)

        detail = get_vas_order(order.name)
        self.assertEqual(len(detail["activities"]), 2)
