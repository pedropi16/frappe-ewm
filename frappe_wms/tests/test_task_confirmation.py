import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task


class TestTaskConfirmation(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-TASK-WH"
        cls.bin_a = "WMS-TEST-TASK-WH-A"
        cls.bin_b = "WMS-TEST-TASK-WH-B"
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
        if not frappe.db.exists("Handling Unit Type", "TEST-TASK-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-TASK-PALLET", "hu_type_name": "Test Task Pallet"}).insert(ignore_permissions=True)

    def _make_task(self, **overrides):
        payload = {
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 10, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        }
        payload.update(overrides)
        task = frappe.get_doc(payload)
        task.insert(ignore_permissions=True)
        return task

    def test_partial_confirmation_keeps_task_open_for_remainder(self):
        task = self._make_task()

        first = confirm_task(task.name, confirmed_quantity=4)
        self.assertEqual(first["status"], "Partially Confirmed")
        task.reload()
        self.assertEqual(task.status, "Partially Confirmed")
        self.assertEqual(task.confirmed_quantity, 4)
        self.assertEqual(task.docstatus, 0)

        second = confirm_task(task.name)
        self.assertEqual(second["status"], "Confirmed")
        self.assertEqual(second["quantity"], 6)
        task.reload()
        self.assertEqual(task.status, "Confirmed")
        self.assertEqual(task.confirmed_quantity, 10)
        self.assertEqual(task.docstatus, 1)

        ledger_qty = frappe.db.sql(
            "select count(*), sum(abs(quantity)) from `tabWMS Stock Ledger Entry` where warehouse_task=%s and storage_bin=%s",
            (task.name, self.bin_b),
        )[0]
        self.assertEqual(ledger_qty[0], 2, "each partial confirmation should post its own ledger entries")
        self.assertEqual(ledger_qty[1], 10)

    def test_overconfirming_beyond_planned_quantity_is_rejected(self):
        task = self._make_task()
        confirm_task(task.name, confirmed_quantity=8)
        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, confirmed_quantity=5)

    def test_move_top_hu_cascades_bin_to_descendants(self):
        parent = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-TASK-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"})
        parent.insert(ignore_permissions=True)
        child = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-TASK-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "parent_hu": parent.name, "status": "Open"})
        child.flags.wms_service_update = True
        child.insert(ignore_permissions=True)

        self.assertEqual(frappe.db.get_value("Handling Unit", child.name, "top_hu"), parent.name)

        task = self._make_task(source_hu=child.name, destination_hu=child.name, move_top_hu=1, planned_quantity=1)
        confirm_task(task.name, confirmed_quantity=1)

        self.assertEqual(frappe.db.get_value("Handling Unit", parent.name, "current_bin"), self.bin_b)
        self.assertEqual(frappe.db.get_value("Handling Unit", child.name, "current_bin"), self.bin_b)
