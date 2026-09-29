import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.patches.v0_2.backfill_unqueued_open_tasks import execute as backfill_unqueued_open_tasks


class TestBackfillUnqueuedOpenTasks(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "BKFL-TEST-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "Bulk",
                "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-DOCK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "DOCK", "storage_type_name": "Dock",
                "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.dock_bin = f"{cls.warehouse}-DOCK"
        if not frappe.db.exists("Storage Bin", cls.dock_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.dock_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-DOCK", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        cls.bulk_bin = f"{cls.warehouse}-BULK-A"
        if not frappe.db.exists("Storage Bin", cls.bulk_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bulk_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-BULK", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        cls.queue = f"BKFL-PUTAWAY-Q"
        if not frappe.db.exists("Warehouse Queue", cls.queue):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": cls.queue, "queue_name": cls.queue,
                "warehouse": cls.warehouse, "activity": "Putaway", "storage_type": f"{cls.warehouse}-BULK", "active": 1}).insert(ignore_permissions=True)
        cls.item = "BKFL-TEST-ITEM"
        if not frappe.db.exists("Item", cls.item):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": cls.item, "item_name": cls.item, "item_group": item_group, "stock_uom": "Nos", "is_stock_item": 1}).insert(ignore_permissions=True)

    def _make_orphan_task(self):
        # Deliberately bypasses attach_task, the way every pre-fix production Putaway task did:
        # inserted straight with warehouse_order/queue left blank, exactly the dead-end state the
        # RF app's own "Putaway tasks" list still shows as workable "Open" work (confirmed live)
        # even though pull_next_warehouse_order can never actually serve it.
        return frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Putaway", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": "Nos",
            "source_bin": self.dock_bin, "destination_bin": self.bulk_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "101", "priority": "Normal", "status": "Open",
        }).insert(ignore_permissions=True)

    def test_backfill_attaches_orphaned_open_task_to_its_matching_queue(self):
        task = self._make_orphan_task()
        self.assertFalse(task.warehouse_order)

        backfill_unqueued_open_tasks()

        task.reload()
        self.assertEqual(task.queue, self.queue)
        self.assertIsNotNone(task.warehouse_order, "the backfill must attach an orphaned Open task to a Warehouse Order once a matching queue exists")

    def test_backfill_leaves_already_queued_tasks_untouched(self):
        from frappe_wms.services.warehouse_order import attach_task
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Putaway", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": "Nos",
            "source_bin": self.dock_bin, "destination_bin": self.bulk_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "101", "priority": "Normal", "status": "Open",
        })
        attach_task(task, frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        original_wo = task.warehouse_order
        self.assertTrue(original_wo)

        backfill_unqueued_open_tasks()

        task.reload()
        self.assertEqual(task.warehouse_order, original_wo, "a task already attached to a Warehouse Order must not be reassigned to a new one")
