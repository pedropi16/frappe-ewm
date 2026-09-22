import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import raise_exception
from frappe_wms.api.inventory import request_direct_replenishment
from frappe_wms.services.stock import post_entries


class TestReplenishment(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-REPLEN-WH"
        cls.pick_bin = "WMS-TEST-REPLEN-WH-PICK"
        cls.bulk_bin = "WMS-TEST-REPLEN-WH-BULK"
        cls.no_rule_pick_bin = "WMS-TEST-REPLEN-WH-PICK2"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-PICK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "PICK", "storage_type_name": "Pick Face", "storage_role": "Picking", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "Bulk", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, storage_type in ((cls.pick_bin, f"{cls.warehouse}-PICK"), (cls.no_rule_pick_bin, f"{cls.warehouse}-PICK"), (cls.bulk_bin, f"{cls.warehouse}-BULK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": storage_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Exception Code", "WMS-TEST-REPLEN-OOS"):
            frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": "WMS-TEST-REPLEN-OOS", "exception_name": "Test OOS", "category": "Stock", "allows_quantity_change": 1, "follow_up_action": "Create Follow-up Task", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Replenishment Rule", {"warehouse": cls.warehouse, "product": cls.item, "storage_bin": cls.pick_bin}):
            frappe.get_doc({"doctype": "Replenishment Rule", "warehouse": cls.warehouse, "product": cls.item, "storage_bin": cls.pick_bin,
                "stock_type": "AVAILABLE", "minimum_quantity": 5, "target_quantity": 20, "source_storage_type": f"{cls.warehouse}-BULK",
                "priority": "Normal", "active": 1}).insert(ignore_permissions=True)

    def _seed_bulk_stock(self, item, qty):
        post_entries([{
            "warehouse": self.warehouse, "product": item, "storage_bin": self.bulk_bin,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701",
        }], "Storage Bin", self.bulk_bin, f"test-replen-seed:{frappe.generate_hash(length=8)}")

    def _make_pick_task(self, source_bin, planned_quantity=10):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Pick", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": planned_quantity, "stock_uom": self.uom,
            "source_bin": source_bin, "destination_bin": self.bulk_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "401", "priority": "Normal", "status": "Open",
        })
        task.insert(ignore_permissions=True)
        return task

    def test_pick_denial_with_matching_rule_creates_order_related_replenishment(self):
        self._seed_bulk_stock(self.item, 50)
        task = self._make_pick_task(self.pick_bin)

        raise_exception(task.name, "WMS-TEST-REPLEN-OOS", remarks="empty bin", revised_quantity=0)

        requests = frappe.get_all("Warehouse Request", filters={"reference_doctype": "Warehouse Task", "reference_name": task.name}, fields=["name", "source_bin", "destination_bin", "requested_quantity"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].source_bin, self.bulk_bin)
        self.assertEqual(requests[0].destination_bin, self.pick_bin)
        self.assertGreater(requests[0].requested_quantity, 0)

        tasks = frappe.get_all("Warehouse Task", filters={"warehouse_request": requests[0].name})
        self.assertEqual(len(tasks), 1)

    def test_pick_denial_with_no_matching_rule_creates_no_replenishment(self):
        task = self._make_pick_task(self.no_rule_pick_bin)
        raise_exception(task.name, "WMS-TEST-REPLEN-OOS", remarks="empty bin, no rule configured", revised_quantity=0)
        requests = frappe.get_all("Warehouse Request", filters={"reference_doctype": "Warehouse Task", "reference_name": task.name})
        self.assertEqual(len(requests), 0)

    def test_direct_replenishment_bypasses_minimum_quantity_threshold(self):
        # There is stock in the pick bin already (above the rule's minimum), which would
        # make the scheduled scan skip it entirely - direct replenishment must still work.
        post_entries([{
            "warehouse": self.warehouse, "product": self.item, "storage_bin": self.pick_bin,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 100, "movement_type": "701",
        }], "Storage Bin", self.pick_bin, f"test-replen-direct-seed:{frappe.generate_hash(length=8)}")
        self._seed_bulk_stock(self.item, 30)

        result = request_direct_replenishment(self.warehouse, self.item, self.pick_bin, "AVAILABLE", 15, f"{self.warehouse}-BULK")
        self.assertTrue(result["warehouse_request"])
        request = frappe.get_doc("Warehouse Request", result["warehouse_request"])
        self.assertEqual(request.requested_quantity, 15)
        self.assertEqual(request.destination_bin, self.pick_bin)

    def test_direct_replenishment_without_source_stock_is_rejected(self):
        with self.assertRaises(frappe.ValidationError):
            request_direct_replenishment(self.warehouse, self.item, self.pick_bin, "AVAILABLE", 10, f"{self.warehouse}-EMPTY")
