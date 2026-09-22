import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.warehouse_order import attach_task, join_queue, leave_queue, list_queues, pull_next_warehouse_order
from frappe_wms.services.bin_assignment import search_bins_for_assignment, mass_assign_activity_area


class TestActivityAreaQueues(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "AAQ-TEST-WH"
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.uom = "Nos"
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Activity Area", f"{cls.warehouse}-AA1"):
            frappe.get_doc({"doctype": "Activity Area", "warehouse": cls.warehouse, "area_code": "AA1", "area_name": "Fast Movers", "active": 1}).insert(ignore_permissions=True)
        cls.bin_with_area = f"{cls.warehouse}-BIN-AREA"
        cls.bin_plain = f"{cls.warehouse}-BIN-PLAIN"
        if not frappe.db.exists("Storage Bin", cls.bin_with_area):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin_with_area, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A",
                "activity_area": f"{cls.warehouse}-AA1", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.bin_plain):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin_plain, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A",
                "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        cls.bin_dest = f"{cls.warehouse}-BIN-DEST"
        if not frappe.db.exists("Storage Bin", cls.bin_dest):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin_dest, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A",
                "active": 1, "sequence": 3}).insert(ignore_permissions=True)
        cls.item = f"AAQ-TEST-ITEM"
        if not frappe.db.exists("Item", cls.item):
            frappe.get_doc({"doctype": "Item", "item_code": cls.item, "item_name": cls.item, "item_group": cls.item_group, "stock_uom": cls.uom, "is_stock_item": 1}).insert(ignore_permissions=True)

    def _make_task(self, source_bin, batch_key=None):
        # attach_task resolves storage_type/activity_area from the first of (source_bin,
        # destination_bin) that's set and has a storage_type - since every bin here always has
        # one, source_bin is what actually drives routing; destination is held fixed and
        # neutral so only the source side varies across test cases.
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": self.uom,
            "source_bin": source_bin, "destination_bin": self.bin_dest,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        attach_task(task, batch_key or frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        return task

    def test_queue_matching_activity_area_wins_over_storage_type_only(self):
        queue_storage = f"AAQ-Q-STORAGE-{frappe.generate_hash(length=6)}"
        queue_area = f"AAQ-Q-AREA-{frappe.generate_hash(length=6)}"
        frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": queue_storage, "queue_name": queue_storage,
            "warehouse": self.warehouse, "activity": "Internal Move", "storage_type": f"{self.warehouse}-A", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": queue_area, "queue_name": queue_area,
            "warehouse": self.warehouse, "activity": "Internal Move", "activity_area": f"{self.warehouse}-AA1", "active": 1}).insert(ignore_permissions=True)

        task_plain = self._make_task(self.bin_plain)
        task_area = self._make_task(self.bin_with_area)
        self.assertEqual(task_plain.queue, queue_storage)
        self.assertEqual(task_area.queue, queue_area)

    def test_resource_group_fallback_when_no_current_queue_joined(self):
        # A dedicated storage type/bin (not the shared class-level ones) - other test methods
        # in this class leave their own Warehouse Queues behind (no rollback between methods),
        # and a queue matching this test's storage_type from a sibling test would otherwise
        # win the tier-2 match before this test's own blank-fallback queue is ever reached.
        suffix = frappe.generate_hash(length=6)
        storage_type = f"{self.warehouse}-RG{suffix}"
        frappe.get_doc({"doctype": "Storage Type", "warehouse": self.warehouse, "storage_type_code": f"RG{suffix}", "storage_type_name": storage_type,
            "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        source_bin = f"{self.warehouse}-RGBIN-{suffix}"
        frappe.get_doc({"doctype": "Storage Bin", "bin_code": source_bin, "warehouse": self.warehouse, "storage_type": storage_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)

        group = f"AAQ-RG-{suffix}"
        frappe.get_doc({"doctype": "WMS Resource Group", "group_code": group, "group_name": group, "warehouse": self.warehouse, "active": 1}).insert(ignore_permissions=True)
        queue_name = f"AAQ-Q-RG-{suffix}"
        frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": queue_name, "queue_name": queue_name,
            "warehouse": self.warehouse, "activity": "Internal Move", "resource_group": group, "active": 1}).insert(ignore_permissions=True)
        resource_code = f"AAQ-RES-{suffix}"
        frappe.get_doc({"doctype": "WMS Resource", "resource_code": resource_code, "warehouse": self.warehouse,
            "resource_type": "Operator", "resource_group": group, "active": 1}).insert(ignore_permissions=True)

        task = self._make_task(source_bin, batch_key=frappe.generate_hash(length=10))
        self.assertEqual(task.assigned_resource, resource_code)

    def test_pull_and_list_queues_scoped_to_resource_group(self):
        group_a = f"AAQ-RGA-{frappe.generate_hash(length=6)}"
        group_b = f"AAQ-RGB-{frappe.generate_hash(length=6)}"
        for g in (group_a, group_b):
            frappe.get_doc({"doctype": "WMS Resource Group", "group_code": g, "group_name": g, "warehouse": self.warehouse, "active": 1}).insert(ignore_permissions=True)
        queue_a1 = f"AAQ-QA1-{frappe.generate_hash(length=6)}"
        queue_a2 = f"AAQ-QA2-{frappe.generate_hash(length=6)}"
        queue_b1 = f"AAQ-QB1-{frappe.generate_hash(length=6)}"
        for q, g in ((queue_a1, group_a), (queue_a2, group_a), (queue_b1, group_b)):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": q, "queue_name": q,
                "warehouse": self.warehouse, "activity": "Internal Move", "resource_group": g, "active": 1}).insert(ignore_permissions=True)

        user = frappe.session.user
        resource_code = f"AAQ-PULLRES-{frappe.generate_hash(length=6)}"
        frappe.get_doc({"doctype": "WMS Resource", "resource_code": resource_code, "warehouse": self.warehouse,
            "resource_type": "Operator", "resource_group": group_a, "user": user, "active": 1}).insert(ignore_permissions=True)

        # list_queues scoped to group A only
        queues = list_queues(warehouse=self.warehouse, user=user)
        queue_names = {q.name for q in queues}
        self.assertIn(queue_a1, queue_names)
        self.assertIn(queue_a2, queue_names)
        self.assertNotIn(queue_b1, queue_names)

        # joining a queue outside my resource group is rejected
        with self.assertRaises(frappe.ValidationError):
            join_queue(queue_b1, user=user)

        # unjoined: pull_next_warehouse_order can pull from either of my group's queues
        wo1 = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.warehouse, "activity": "Internal Move",
            "queue": queue_a2, "batch_key": frappe.generate_hash(length=10), "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
        pulled = pull_next_warehouse_order(user=user)
        self.assertEqual(pulled, wo1.name)

        # once joined to one specific queue, pulling is restricted to it
        frappe.db.set_value("Warehouse Order", wo1.name, "assigned_resource", None)
        join_queue(queue_a1, user=user)
        wo2 = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.warehouse, "activity": "Internal Move",
            "queue": queue_a2, "batch_key": frappe.generate_hash(length=10), "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
        self.assertIsNone(pull_next_warehouse_order(user=user))
        leave_queue(user=user)

    def test_mass_assign_activity_area_updates_selected_bins(self):
        area = f"{self.warehouse}-AA1"
        bin1 = f"{self.warehouse}-MASS-1"
        bin2 = f"{self.warehouse}-MASS-2"
        for b in (bin1, bin2):
            if not frappe.db.exists("Storage Bin", b):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": b, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

        found = search_bins_for_assignment(self.warehouse, storage_type=f"{self.warehouse}-A")
        self.assertTrue(any(b.name == bin1 for b in found))

        result = mass_assign_activity_area([bin1, bin2], area)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(frappe.db.get_value("Storage Bin", bin1, "activity_area"), area)
        self.assertEqual(frappe.db.get_value("Storage Bin", bin2, "activity_area"), area)
