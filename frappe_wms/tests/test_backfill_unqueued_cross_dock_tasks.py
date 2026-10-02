import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.patches.v0_2.backfill_unqueued_cross_dock_tasks import execute as backfill_unqueued_cross_dock_tasks


class TestBackfillUnqueuedCrossDockTasks(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "BKFLCD-TEST-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-DOCK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "DOCK", "storage_type_name": "Dock",
                "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-DOOR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "DOOR", "storage_type_name": "Door",
                "storage_role": "Door", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.dock_bin = f"{cls.warehouse}-DOCK"
        if not frappe.db.exists("Storage Bin", cls.dock_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.dock_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-DOCK", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        cls.door_bin = f"{cls.warehouse}-DOOR1"
        if not frappe.db.exists("Storage Bin", cls.door_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.door_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-DOOR", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        cls.item = "BKFLCD-TEST-ITEM"
        if not frappe.db.exists("Item", cls.item):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": cls.item, "item_name": cls.item, "item_group": item_group, "stock_uom": "Nos", "is_stock_item": 1}).insert(ignore_permissions=True)

    def setUp(self):
        # Mirrors production's pre-fix state exactly: no Warehouse Queue for this warehouse's
        # Cross Dock activity at all (every test run starts from that same no-queue state, same
        # as this warehouse before the patch ever ran on it).
        frappe.db.delete("Warehouse Queue", {"warehouse": self.warehouse, "activity": "Cross Dock"})

    def _make_orphan_task(self):
        # Reproduces exactly what services/receipt.py's find_cross_dock_demand left behind on
        # every one of these on this site: attach_task ran, found no queue (none existed for
        # Cross Dock), and no-op'd by design - the task inserts fine but warehouse_order/queue
        # stay blank, an "Open" task pull_next_warehouse_order can never actually serve.
        return frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Cross Dock", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": "Nos",
            "source_bin": self.dock_bin, "destination_bin": self.door_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "101", "priority": "High", "status": "Open",
        }).insert(ignore_permissions=True)

    def test_backfill_creates_the_missing_queue_and_attaches_orphaned_tasks(self):
        self.assertFalse(frappe.db.exists("Warehouse Queue", {"warehouse": self.warehouse, "activity": "Cross Dock"}))
        task = self._make_orphan_task()
        self.assertFalse(task.warehouse_order)

        backfill_unqueued_cross_dock_tasks()

        queue = frappe.db.get_value("Warehouse Queue", {"warehouse": self.warehouse, "activity": "Cross Dock"}, "name")
        self.assertTrue(queue, "the backfill must create a Cross Dock queue where none exists")
        task.reload()
        self.assertEqual(task.queue, queue)
        self.assertIsNotNone(task.warehouse_order, "the backfill must attach an orphaned Cross Dock task to a Warehouse Order once a queue exists")

    def test_backfill_skips_a_task_whose_source_hu_no_longer_exists_without_blocking_the_rest(self):
        # Found live on production: 117 of 147 Cross Dock tasks' source_hu pointed at a Handling
        # Unit that no longer exists (created fine originally, deleted later by an unrelated
        # data wipe/reseed) - task.save()'s own link validation throws LinkValidationError for
        # these, same as for any dangling Link field. One bad record must not abort the whole
        # backfill and leave even the healthy tasks unqueued.
        if not frappe.db.exists("Handling Unit Type", "BKFLCD-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "BKFLCD-PALLET", "hu_type_name": "Backfill CD Pallet"}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "BKFLCD-PALLET",
            "warehouse": self.warehouse, "current_bin": self.dock_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        bad_task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Cross Dock", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": "Nos",
            "source_bin": self.dock_bin, "destination_bin": self.door_bin, "source_hu": hu.name,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "101", "priority": "High", "status": "Open",
        }).insert(ignore_permissions=True)
        frappe.delete_doc("Handling Unit", hu.name, ignore_permissions=True, force=True)
        good_task = self._make_orphan_task()

        backfill_unqueued_cross_dock_tasks()

        bad_task.reload()
        self.assertFalse(bad_task.warehouse_order, "a task that can never actually save must not be left half-attached")
        good_task.reload()
        self.assertTrue(good_task.warehouse_order, "one bad record must not block every other orphan from being fixed")

    def test_backfill_is_idempotent_and_leaves_already_queued_tasks_untouched(self):
        task = self._make_orphan_task()

        backfill_unqueued_cross_dock_tasks()
        task.reload()
        first_wo, first_queue = task.warehouse_order, task.queue
        self.assertTrue(first_wo)

        # Rerunning (e.g. a second `bench migrate` attempt, or running the patch's execute()
        # directly again while debugging) must not create a duplicate queue or reassign an
        # already-attached task to a new Warehouse Order.
        backfill_unqueued_cross_dock_tasks()

        self.assertEqual(frappe.db.count("Warehouse Queue", {"warehouse": self.warehouse, "activity": "Cross Dock"}), 1)
        task.reload()
        self.assertEqual(task.warehouse_order, first_wo)
        self.assertEqual(task.queue, first_queue)
