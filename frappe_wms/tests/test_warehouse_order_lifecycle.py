import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import confirm_task, raise_exception
from frappe_wms.services.warehouse_order import attach_task, sync_warehouse_order, pull_next_warehouse_order, block_warehouse_order, resume_warehouse_order


class TestWarehouseOrderLifecycle(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WOL-TEST-WH"
        cls.bin_a = f"{cls.warehouse}-A"
        cls.bin_b = f"{cls.warehouse}-B"
        cls.bin_c = f"{cls.warehouse}-C"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse,
                "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A",
                "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, seq in ((cls.bin_a, 1), (cls.bin_b, 2), (cls.bin_c, 3)):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A",
                    "active": 1, "sequence": seq}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "WOL-TEST-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "WOL-TEST-QUEUE", "queue_name": "WOL Test Queue",
                "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Exception Code", "WOL-TEST-EXC"):
            frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": "WOL-TEST-EXC", "exception_name": "WOL Test Exception",
                "category": "Task", "active": 1}).insert(ignore_permissions=True)

        cls.picker_email = "wol-picker@example.com"
        cls.supervisor_email = "wol-supervisor@example.com"
        for email, roles in ((cls.picker_email, ["WMS Picker", "WMS Operator"]), (cls.supervisor_email, ["WMS Supervisor"])):
            if not frappe.db.exists("User", email):
                frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}).insert(ignore_permissions=True)
                frappe.get_doc("User", email).add_roles(*roles)
        if not frappe.db.exists("WMS Resource", "WOL-TEST-RESOURCE"):
            frappe.get_doc({"doctype": "WMS Resource", "resource_code": "WOL-TEST-RESOURCE", "warehouse": cls.warehouse,
                "resource_type": "Operator", "user": cls.picker_email, "current_queue": "WOL-TEST-QUEUE", "active": 1}).insert(ignore_permissions=True)

    def _make_task(self, batch_key=None, sequence=None):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": self.uom,
            "source_bin": self.bin_a, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open", "sequence": sequence,
        })
        attach_task(task, batch_key or frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        return task

    def test_new_warehouse_order_is_open_and_unassigned(self):
        task = self._make_task()
        wo = frappe.get_doc("Warehouse Order", task.warehouse_order)
        self.assertEqual(wo.status, "Open")
        self.assertIn(wo.assigned_resource, ("", None))
        self.assertIn(task.assigned_resource, ("", None))

    def test_pull_next_warehouse_order_still_claims_it(self):
        # A dedicated queue/bin (not the shared class-level ones) - other test methods in this
        # class leave their own Open, unassigned Warehouse Orders behind in WOL-TEST-QUEUE (no
        # rollback between methods), which would otherwise be an older, higher-priority pull
        # candidate than the one this test creates.
        suffix = frappe.generate_hash(length=6)
        storage_type = f"{self.warehouse}-PULL{suffix}"
        frappe.get_doc({"doctype": "Storage Type", "warehouse": self.warehouse, "storage_type_code": f"PULL{suffix}", "storage_type_name": storage_type,
            "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        source_bin = f"{self.warehouse}-PULLBIN-{suffix}"
        frappe.get_doc({"doctype": "Storage Bin", "bin_code": source_bin, "warehouse": self.warehouse, "storage_type": storage_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        queue_name = f"WOL-Q-PULL-{suffix}"
        frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": queue_name, "queue_name": queue_name,
            "warehouse": self.warehouse, "activity": "Internal Move", "storage_type": storage_type, "active": 1}).insert(ignore_permissions=True)
        frappe.db.set_value("WMS Resource", "WOL-TEST-RESOURCE", "current_queue", queue_name)

        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 1, "stock_uom": self.uom,
            "source_bin": source_bin, "destination_bin": self.bin_b,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        attach_task(task, frappe.generate_hash(length=10))
        task.insert(ignore_permissions=True)
        self.assertEqual(task.queue, queue_name)

        wo_name = pull_next_warehouse_order(user=self.picker_email)
        self.assertEqual(wo_name, task.warehouse_order)
        wo = frappe.get_doc("Warehouse Order", wo_name)
        self.assertEqual(wo.assigned_resource, "WOL-TEST-RESOURCE")
        self.assertEqual(wo.status, "Assigned")
        task.reload()
        self.assertEqual(task.assigned_resource, "WOL-TEST-RESOURCE")
        frappe.db.set_value("WMS Resource", "WOL-TEST-RESOURCE", "current_queue", "WOL-TEST-QUEUE")

    def test_confirming_a_task_claims_its_unassigned_warehouse_order(self):
        task = self._make_task()
        frappe.set_user(self.picker_email)
        try:
            confirm_task(task.name, confirmed_quantity=1)
        finally:
            frappe.set_user("Administrator")
        wo = frappe.get_doc("Warehouse Order", task.warehouse_order)
        self.assertEqual(wo.assigned_resource, "WOL-TEST-RESOURCE")

    def test_warehouse_order_becomes_blocked_on_lead_task_exception_and_clears_on_resolve(self):
        batch_key = frappe.generate_hash(length=10)
        first = self._make_task(batch_key=batch_key, sequence=1)
        self._make_task(batch_key=batch_key, sequence=2)
        wo_name = first.warehouse_order

        raise_exception(first.name, "WOL-TEST-EXC", remarks="blocked for test")
        wo = frappe.get_doc("Warehouse Order", wo_name)
        self.assertEqual(wo.status, "Blocked")
        self.assertTrue(wo.blocking_reason)

        frappe.db.set_value("Warehouse Task", first.name, {"status": "Open", "blocking_reason": None}, update_modified=True)
        sync_warehouse_order(wo_name)
        wo.reload()
        self.assertNotEqual(wo.status, "Blocked")

    def test_block_and_resume_require_supervisor_and_hold_survives_sync(self):
        task = self._make_task()
        wo_name = task.warehouse_order

        with self.assertRaises(frappe.PermissionError):
            frappe.set_user(self.picker_email)
            try:
                block_warehouse_order(wo_name, "should be rejected")
            finally:
                frappe.set_user("Administrator")

        frappe.set_user(self.supervisor_email)
        try:
            block_warehouse_order(wo_name, "supervisor pause")
        finally:
            frappe.set_user("Administrator")
        wo = frappe.get_doc("Warehouse Order", wo_name)
        self.assertEqual(wo.status, "On Hold")
        self.assertEqual(wo.blocking_reason, "supervisor pause")

        # An automatic recompute must never silently clear a deliberate pause.
        sync_warehouse_order(wo_name)
        wo.reload()
        self.assertEqual(wo.status, "On Hold")

        with self.assertRaises(frappe.PermissionError):
            frappe.set_user(self.picker_email)
            try:
                resume_warehouse_order(wo_name)
            finally:
                frappe.set_user("Administrator")

        frappe.set_user(self.supervisor_email)
        try:
            resume_warehouse_order(wo_name)
        finally:
            frappe.set_user("Administrator")
        wo.reload()
        self.assertNotEqual(wo.status, "On Hold")
