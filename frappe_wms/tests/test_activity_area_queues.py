from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.warehouse_order import attach_task, join_queue, leave_queue, list_queues, pull_next_warehouse_order
from frappe_wms.services.bin_assignment import search_bins_for_assignment, mass_assign_activity_area
from frappe_wms.services.task import list_my_tasks


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

    def test_putaway_task_routes_by_destination_bin_not_source(self):
        # Reproduces the production bug directly: a Putaway task's source_bin is always some
        # generic receiving dock, never the zone a queue is actually scoped to - source_bin must
        # NOT win here the way it correctly does for Internal Move (see _make_task's own comment
        # and the test right below this one).
        dock_code = f"DOCK{frappe.generate_hash(length=6)}"
        dock_storage_type = f"{self.warehouse}-{dock_code}"
        frappe.get_doc({"doctype": "Storage Type", "warehouse": self.warehouse, "storage_type_code": dock_code, "storage_type_name": dock_code,
            "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        dock_bin = f"{self.warehouse}-DOCKBIN-{frappe.generate_hash(length=6)}"
        frappe.get_doc({"doctype": "Storage Bin", "bin_code": dock_bin, "warehouse": self.warehouse, "storage_type": dock_storage_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        putaway_queue = f"AAQ-PUTAWAY-Q-{frappe.generate_hash(length=6)}"
        frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": putaway_queue, "queue_name": putaway_queue,
            "warehouse": self.warehouse, "activity": "Putaway", "storage_type": f"{self.warehouse}-A", "active": 1}).insert(ignore_permissions=True)

        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Putaway", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": self.uom,
            "source_bin": dock_bin, "destination_bin": self.bin_plain,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "101", "priority": "Normal", "status": "Open",
        })
        attach_task(task, frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        self.assertEqual(task.queue, putaway_queue, "Putaway must route by destination_bin's storage type, not the source dock's")
        self.assertIsNotNone(task.warehouse_order, "a Putaway task with a matching queue must not be left unqueued")

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
        email = f"aaq-rg-{suffix}@example.com"
        if not frappe.db.exists("User", email):
            frappe.get_doc({"doctype": "User", "email": email, "first_name": "AAQ RG Test", "send_welcome_email": 0}).insert(ignore_permissions=True)
            frappe.get_doc("User", email).add_roles("WMS Picker")
        resource_code = f"AAQ-RES-{suffix}"
        frappe.get_doc({"doctype": "WMS Resource", "resource_code": resource_code, "warehouse": self.warehouse,
            "resource_type": "Operator", "user": email, "resource_group": group, "active": 1}).insert(ignore_permissions=True)

        task = self._make_task(source_bin, batch_key=frappe.generate_hash(length=10))
        # The task is never auto-assigned to a resource - it stays open and unassigned,
        # discoverable by anyone whose Resource Group covers the queue it landed in, without
        # ever having manually joined that queue (current_queue left blank on the resource).
        self.assertIn(task.assigned_resource, ("", None))
        result = list_my_tasks(user=email)
        self.assertIn(task.name, [t["name"] for t in result["tasks"]])

    def test_my_own_assigned_task_is_never_crowded_out_by_unrelated_higher_ranked_ones(self):
        # Reproduced live: list_my_tasks used to fetch one global top-200 (ordered by priority
        # desc, wave asc, sequence asc, creation asc across the WHOLE warehouse) and filter down
        # to "mine" in Python afterward - fine while the warehouse-wide open-task count stayed
        # under ~200, but once a large backlog built up (thousands of Putaway/Cross-Dock tasks
        # from a scaled load test), a resource's own freshly-assigned task - genuinely assigned
        # to them seconds earlier by pull_next_warehouse_order - could rank behind enough older/
        # higher-priority unrelated tasks to never make it into that shared window at all. "My
        # tasks" silently showed none of it: no error, no navigation, nothing to explain why.
        # Four Urgent-priority tasks assigned to someone else stand in for "a large unrelated
        # backlog that ranks ahead" - the fix queries "mine" independently of any such crowding,
        # by construction, so the exact count doesn't matter; a handful demonstrates it.
        suffix = frappe.generate_hash(length=6)
        other_user = frappe.session.user
        resource_code = f"AAQ-CROWD-RES-{suffix}"
        resource = frappe.get_doc({"doctype": "WMS Resource", "resource_code": resource_code, "warehouse": self.warehouse,
            "resource_type": "Operator", "user": other_user, "active": 1}).insert(ignore_permissions=True)
        bin_other = f"{self.warehouse}-CROWDBIN-{suffix}"
        frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_other, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        # The old global top-200 only actually starves "mine" out once the warehouse-wide count
        # of higher-ranked rows exceeds that limit - confirmed by running this test against the
        # pre-fix code: it passed right up until this loop's count crossed 200, proving a smaller
        # count would not have caught the regression at all. Inserted directly (bypassing
        # attach_task's own routing/WO-batching work) purely for speed; none of that machinery is
        # what this test is about.
        #
        # Scoped to a queue nobody (not even `resource`) is eligible for, and unassigned - stands
        # in for "the rest of the warehouse's unrelated backlog", invisible to anyone's "mine" or
        # "unclaimed" query, old code's bug aside. Two things this must NOT be: (1) assigned to
        # `resource` itself - that would flood `resource`'s OWN "mine" query with its own Urgent
        # work ranking ahead of its one Low-priority task, a real but much narrower limit than
        # this test is reproducing; (2) left with no queue at all - "unrouted" work is everyone's
        # by design (see test_resource_group_fallback_when_no_current_queue_joined above), so 205
        # of those would just as easily crowd out *other* tests' own single expected task from
        # their own "unclaimed" window - reproduced live, while writing this test, as a sibling
        # test failure one alphabetical slot away with no code of its own changed at all.
        crowd_queue = f"AAQ-CROWD-Q-{suffix}"
        frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": crowd_queue, "queue_name": crowd_queue,
            "warehouse": self.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)
        crowd_names = []
        for i in range(205):
            t = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
                "product": self.item, "planned_quantity": 1, "stock_uom": self.uom,
                "source_bin": bin_other, "destination_bin": self.bin_dest,
                "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "301",
                "priority": "Urgent", "status": "Open", "queue": crowd_queue,
            }).insert(ignore_permissions=True)
            crowd_names.append(t.name)

        mine = self._make_task(self.bin_plain)
        frappe.db.set_value("Warehouse Task", mine.name, {"priority": "Low", "assigned_resource": resource.name})

        try:
            result = list_my_tasks(user=other_user)
            self.assertIn(mine.name, [t["name"] for t in result["tasks"]],
                "a resource's own assigned task must never be excluded by unrelated higher-ranked ones")
        finally:
            frappe.db.delete("Warehouse Task", {"name": ["in", crowd_names]})

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


class TestPullDeadlockRecovery(IntegrationTestCase):
    # A dedicated, single-test class - not a method on TestActivityAreaQueues above - on purpose:
    # the fix under test calls frappe.db.rollback(), which is correct for the real standalone
    # request pull_next_warehouse_order always runs as, but IntegrationTestCase only isolates
    # *between* classes (one commit at setUpClass, one rollback at teardown), not between test
    # methods within the same class. A mid-test rollback() here would otherwise wipe out whatever
    # a sibling test method in the same class had already set up earlier in the same run -
    # reproduced live (it took out this file's own shared setUpClass warehouse). Being the only
    # test in its class means there is no sibling left to damage, and no need to paper over it
    # with a manual commit() of its own (which introduced a *worse* problem: committing this
    # class's fixtures early bled into other, unrelated test classes' isolation too when run as
    # part of the full suite).
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PDLR-TEST-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)

    def test_pull_returns_no_work_instead_of_raising_on_query_deadlock(self):
        # Reproduces the production bug directly: under real concurrent load, the claim's own
        # conditional UPDATE can raise a genuine QueryDeadlockError (MySQL/MariaDB error
        # 1020/1213) instead of cleanly returning 0 rows - the database detecting the same race
        # the compare-and-set already exists to handle, just via an exception. An earlier fix
        # tried to recover with a per-candidate savepoint and fall through to the next one, but
        # that introduced a second bug: when MySQL picks this transaction as the deadlock
        # *victim* it can discard every savepoint in it, so the savepoint's own cleanup then
        # threw an unrelated "SAVEPOINT ... does not exist" (1305) - also reproduced live. The
        # real fix gives up on the whole pull attempt (not just this one candidate) and returns
        # None, the same outcome as "no work waiting right now" - instead of either raw 500.
        queue = f"PDLR-Q-{frappe.generate_hash(length=6)}"
        frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": queue, "queue_name": queue,
            "warehouse": self.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)
        user = frappe.session.user
        resource_code = f"PDLR-RES-{frappe.generate_hash(length=6)}"
        frappe.get_doc({"doctype": "WMS Resource", "resource_code": resource_code, "warehouse": self.warehouse,
            "resource_type": "Operator", "user": user, "active": 1}).insert(ignore_permissions=True)
        join_queue(queue, user=user)

        # Urgent vs Normal (not creation order, which can tie at test speed) guarantees
        # wo_deadlocked is always the first candidate _by_priority_then_age tries.
        wo_deadlocked = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.warehouse, "activity": "Internal Move",
            "queue": queue, "batch_key": frappe.generate_hash(length=10), "priority": "Urgent", "status": "Open"}).insert(ignore_permissions=True)
        wo_next = frappe.get_doc({"doctype": "Warehouse Order", "warehouse": self.warehouse, "activity": "Internal Move",
            "queue": queue, "batch_key": frappe.generate_hash(length=10), "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)

        real_sql = frappe.db.sql
        def sql_that_deadlocks_once(query, *args, **kwargs):
            if "update `tabWarehouse Order`" in query and wo_deadlocked.name in (args[0] if args else ()):
                raise frappe.QueryDeadlockError("simulated deadlock")
            return real_sql(query, *args, **kwargs)

        with patch.object(frappe.db, "sql", side_effect=sql_that_deadlocks_once):
            pulled = pull_next_warehouse_order(user=user)

        # No reload()/state assertions on wo_deadlocked or wo_next here on purpose: the fix's own
        # rollback() means their post-call state in *this* test's shared transaction is exactly
        # as uncertain as a real request's would be after a genuine deadlock-victim rollback -
        # the one thing actually guaranteed, in both cases, is the function's own return value.
        self.assertIsNone(pulled, "a deadlock must be reported as no work available, not raise or hand back a different candidate")
