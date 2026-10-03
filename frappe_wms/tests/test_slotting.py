import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.stock import post_entries
from frappe_wms.services.slotting import analyze_slotting, generate_rearrangement_tasks
from frappe_wms.services.warehouse_order import pull_next_warehouse_order, join_queue


class TestSlotting(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "SLOT-TEST-WH"
        cls.pick_bin = f"{cls.warehouse}-PICK-BIN"
        cls.bulk_bin = f"{cls.warehouse}-BULK-BIN"
        cls.uom = "Nos"
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in (("PICK", "Picking"), ("BULK", "Storage")):
            if not frappe.db.exists("Storage Type", f"{cls.warehouse}-{code}"):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code, "storage_type_name": code, "storage_role": role,
                    "capacity_check_method": "HU Count", "hu_managed": 0, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.pick_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.pick_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-PICK", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.bulk_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bulk_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-BULK", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        # Blank destination_storage_type so determine_destination_bin falls back to the
        # product's own preferred_storage_type (P1's documented fallback behavior).
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Internal Move"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Internal Move",
                "active": 1, "priority": 1, "strategy": "Bin Sequence"}).insert(ignore_permissions=True)

    def _make_item(self, suffix):
        item = f"SLOT-TEST-ITEM-{suffix}"
        if not frappe.db.exists("Item", item):
            frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": self.item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item}):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1,
                "preferred_storage_type": f"{self.warehouse}-PICK"}).insert(ignore_permissions=True)
        return item

    def _seed_in_bulk(self, item, quantity):
        post_entries([{"warehouse": self.warehouse, "product": item, "storage_bin": self.bulk_bin, "stock_type": "AVAILABLE",
            "quantity": quantity, "stock_uom": self.uom, "movement_type": "101"}],
            "Storage Bin", self.bulk_bin, f"slot-seed-{item}:{frappe.generate_hash(length=8)}")

    def _post_picks(self, item, count):
        for i in range(count):
            post_entries([{"warehouse": self.warehouse, "product": item, "storage_bin": self.bulk_bin, "stock_type": "AVAILABLE",
                "quantity": -1, "stock_uom": self.uom, "movement_type": "401"}],
                "Storage Bin", self.bulk_bin, f"slot-pick-{item}-{i}:{frappe.generate_hash(length=8)}")

    def test_high_velocity_misplaced_item_is_flagged(self):
        item = self._make_item("A")
        self._seed_in_bulk(item, 100)
        self._post_picks(item, 6)

        recommendations = analyze_slotting(self.warehouse, min_picks=5)
        matching = [r for r in recommendations if r["product"] == item]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["current_bin"], self.bulk_bin)
        self.assertEqual(matching[0]["current_storage_type"], f"{self.warehouse}-BULK")
        self.assertEqual(matching[0]["preferred_storage_type"], f"{self.warehouse}-PICK")
        self.assertEqual(matching[0]["recent_picks"], 6)

    def test_below_min_picks_not_flagged(self):
        item = self._make_item("B")
        self._seed_in_bulk(item, 100)
        self._post_picks(item, 2)

        recommendations = analyze_slotting(self.warehouse, min_picks=5)
        matching = [r for r in recommendations if r["product"] == item]
        self.assertEqual(matching, [])

    def test_generate_rearrangement_tasks_creates_internal_move_task(self):
        item = self._make_item("C")
        self._seed_in_bulk(item, 50)
        self._post_picks(item, 6)

        recommendations = analyze_slotting(self.warehouse, min_picks=5)
        matching = [r for r in recommendations if r["product"] == item]
        self.assertEqual(len(matching), 1)

        created = generate_rearrangement_tasks(self.warehouse, matching)
        self.assertEqual(len(created), 1)
        task = frappe.get_doc("Warehouse Task", created[0])
        self.assertEqual(task.task_type, "Internal Move")
        self.assertEqual(task.product, item)
        self.assertEqual(task.source_bin, self.bulk_bin)
        self.assertEqual(task.destination_bin, self.pick_bin)
        self.assertEqual(task.status, "Open")

    def test_rearrangement_task_is_pullable_from_its_queue(self):
        # The RF Move screen's System Guided half: a rearrangement task Slotting generates is an
        # ordinary queued Warehouse Task, so the same generic pull_next_warehouse_order already
        # serves it once an Internal Move queue exists - no special-casing needed.
        # The queue must exist before the task is created - attach_task resolves it at creation
        # time (same as every other activity), not retroactively.
        if not frappe.db.exists("Warehouse Queue", "SLOT-TEST-MOVE-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "SLOT-TEST-MOVE-QUEUE", "queue_name": "Slot Test Move Queue",
                "warehouse": self.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)

        item = self._make_item("D")
        self._seed_in_bulk(item, 50)
        self._post_picks(item, 6)
        created = generate_rearrangement_tasks(self.warehouse, [r for r in analyze_slotting(self.warehouse, min_picks=5) if r["product"] == item])
        task = frappe.get_doc("Warehouse Task", created[0])
        self.assertEqual(task.queue, "SLOT-TEST-MOVE-QUEUE")
        self.assertTrue(task.warehouse_order, "attach_task must have queued this onto a Warehouse Order")

        tester_email = "slot-mover-tester@example.com"
        if not frappe.db.exists("User", tester_email):
            frappe.get_doc({"doctype": "User", "email": tester_email, "first_name": "Slot Mover Tester", "send_welcome_email": 0}).insert(ignore_permissions=True)
            frappe.get_doc("User", tester_email).add_roles("WMS Operator")
        if not frappe.db.exists("WMS Resource", {"user": tester_email}):
            frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8), "user": tester_email, "warehouse": self.warehouse, "resource_type": "Operator", "active": 1}).insert(ignore_permissions=True)

        frappe.set_user(tester_email)
        try:
            join_queue("SLOT-TEST-MOVE-QUEUE")
            self.assertEqual(pull_next_warehouse_order(), task.warehouse_order)
        finally:
            frappe.set_user("Administrator")
