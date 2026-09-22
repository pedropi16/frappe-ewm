import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task
from frappe_wms.services.warehouse_order import attach_task


class TestPredecessorGate(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-PRED-WH"
        cls.bin_a = "WMS-TEST-PRED-WH-A"
        cls.bin_b = "WMS-TEST-PRED-WH-B"
        cls.bin_c = "WMS-TEST-PRED-WH-C"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.bin_a, cls.bin_b, cls.bin_c):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        for activity, code in (("Unload", "WMS-TEST-PRED-UNLOAD"), ("Putaway", "WMS-TEST-PRED-PUTAWAY")):
            if not frappe.db.exists("Warehouse Queue", code):
                frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": code, "queue_name": code, "warehouse": cls.warehouse, "activity": activity, "active": 1}).insert(ignore_permissions=True)

    def _make_task(self, task_type, source_bin, destination_bin, movement_type, predecessor_task=None, batch_key=None):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": task_type, "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 5, "stock_uom": self.uom,
            "source_bin": source_bin, "destination_bin": destination_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": movement_type, "priority": "Normal", "status": "Open",
            "predecessor_task": predecessor_task,
        })
        attach_task(task, batch_key or frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        return task

    def test_task_with_predecessor_starts_on_hold_and_releases_when_predecessor_confirms(self):
        first = self._make_task("Unload", self.bin_a, self.bin_b, "301")
        second = self._make_task("Putaway", self.bin_b, self.bin_c, "201", predecessor_task=first.name)

        self.assertNotEqual(first.status, "On Hold")
        self.assertEqual(second.status, "On Hold")
        self.assertIn(first.name, second.blocking_reason)

        with self.assertRaises(frappe.ValidationError):
            confirm_task(second.name, confirmed_quantity=5)

        result = confirm_task(first.name, confirmed_quantity=5)
        self.assertIn(second.name, result["released_tasks"])

        second.reload()
        self.assertNotEqual(second.status, "On Hold")
        self.assertIn(second.status, ("Open", "Assigned"))

    def test_predecessor_gate_is_independent_of_warehouse_order_placement(self):
        # First/second land in different Warehouse Orders entirely (different task types
        # attached with no shared batch_key/queue) - the gate must still work across them.
        first = self._make_task("Unload", self.bin_a, self.bin_b, "301")
        second = self._make_task("Putaway", self.bin_b, self.bin_c, "201", predecessor_task=first.name)
        self.assertNotEqual(first.warehouse_order, second.warehouse_order)
        self.assertEqual(second.status, "On Hold")

        confirm_task(first.name, confirmed_quantity=5)
        second.reload()
        self.assertNotEqual(second.status, "On Hold")

    def test_predecessor_task_still_gated_by_its_own_warehouse_order_sequence(self):
        # second depends on first (predecessor gate) AND sits behind an unrelated sibling
        # (sequence 1) in its own Warehouse Order - confirming first alone must not jump it
        # past its own WO's sequence gate.
        batch_key = frappe.generate_hash(length=10)
        first = self._make_task("Unload", self.bin_a, self.bin_b, "301")
        sibling = self._make_task("Putaway", self.bin_a, self.bin_c, "201", batch_key=batch_key)
        second = self._make_task("Putaway", self.bin_b, self.bin_c, "201", predecessor_task=first.name, batch_key=batch_key)
        second.reload()
        self.assertEqual(second.warehouse_order, sibling.warehouse_order)
        self.assertGreater(second.sequence, sibling.sequence)

        confirm_task(first.name, confirmed_quantity=5)
        second.reload()
        self.assertEqual(second.status, "On Hold", "still blocked by the unconfirmed sibling ahead of it in its own Warehouse Order")
