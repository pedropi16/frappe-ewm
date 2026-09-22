import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task, raise_exception


class TestPickDenial(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-DENY-WH"
        cls.bin_a = "WMS-TEST-DENY-WH-A"
        cls.bin_b = "WMS-TEST-DENY-WH-B"
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
        if not frappe.db.exists("WMS Exception Code", "WMS-TEST-DENY-NOQTY"):
            frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": "WMS-TEST-DENY-NOQTY", "exception_name": "No Quantity Change", "category": "Task", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Exception Code", "WMS-TEST-DENY-OOS"):
            frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": "WMS-TEST-DENY-OOS", "exception_name": "Test OOS", "category": "Stock", "allows_quantity_change": 1, "active": 1}).insert(ignore_permissions=True)

    def _make_task(self, **overrides):
        payload = {
            "doctype": "Warehouse Task", "task_type": "Pick", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 10, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "401", "priority": "Normal", "status": "Open",
        }
        payload.update(overrides)
        task = frappe.get_doc(payload)
        task.insert(ignore_permissions=True)
        return task

    def test_revised_quantity_closes_task_when_fully_covered_by_prior_confirmations(self):
        task = self._make_task()
        confirm_task(task.name, confirmed_quantity=4)
        task.reload()
        self.assertEqual(task.status, "Partially Confirmed")

        result = raise_exception(task.name, "WMS-TEST-DENY-OOS", remarks="only 4 found", revised_quantity=4)
        self.assertEqual(result["status"], "Confirmed")

        task.reload()
        self.assertEqual(task.status, "Confirmed")
        self.assertEqual(task.docstatus, 1)
        self.assertEqual(task.planned_quantity, 4)
        self.assertEqual(task.confirmed_quantity, 4)

    def test_revised_quantity_zero_closes_untouched_task(self):
        task = self._make_task()
        result = raise_exception(task.name, "WMS-TEST-DENY-OOS", remarks="nothing found", revised_quantity=0)
        self.assertEqual(result["status"], "Confirmed")
        task.reload()
        self.assertEqual(task.status, "Confirmed")
        self.assertEqual(task.docstatus, 1)
        self.assertEqual(task.planned_quantity, 0)
        self.assertEqual(task.confirmed_quantity, 0)

    def test_revised_quantity_below_already_confirmed_is_rejected(self):
        task = self._make_task()
        confirm_task(task.name, confirmed_quantity=6)
        with self.assertRaises(frappe.ValidationError):
            raise_exception(task.name, "WMS-TEST-DENY-OOS", remarks="too low", revised_quantity=2)

    def test_exception_code_without_quantity_change_flag_behaves_as_before(self):
        task = self._make_task()
        result = raise_exception(task.name, "WMS-TEST-DENY-NOQTY", remarks="just record it", revised_quantity=4)
        self.assertEqual(result["status"], "Exception")
        task.reload()
        self.assertEqual(task.status, "Exception")
        self.assertEqual(task.planned_quantity, 10)
        self.assertEqual(task.docstatus, 0)

    def test_partial_confirmation_still_leaves_task_open_for_remainder_when_no_revision(self):
        task = self._make_task()
        first = confirm_task(task.name, confirmed_quantity=4)
        self.assertEqual(first["status"], "Partially Confirmed")
        second = confirm_task(task.name)
        self.assertEqual(second["status"], "Confirmed")
        task.reload()
        self.assertEqual(task.confirmed_quantity, 10)
