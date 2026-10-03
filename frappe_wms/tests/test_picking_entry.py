import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task
from frappe_wms.services.picking import release_delivery_for_picking, find_pick_tasks
from frappe_wms.services.receipt import find_putaway_tasks


class TestPickingEntry(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-PICKENTRY-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in ((f"{cls.warehouse}-GR", "Receiving"), (f"{cls.warehouse}-BULK", "Storage")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-GR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.stage_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-PICKENTRY-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-PICKENTRY-PALLET", "hu_type_name": "Test Pick Entry Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "WMS-TEST-PICKENTRY-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "WMS-TEST-PICKENTRY-QUEUE", "queue_name": "Test Pick Entry Queue", "warehouse": cls.warehouse, "activity": "Pick", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "WMS-TEST-PICKENTRY-PUTAWAY-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "WMS-TEST-PICKENTRY-PUTAWAY-QUEUE", "queue_name": "Test Putaway Entry Queue", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1}).insert(ignore_permissions=True)

    def _receive(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-PICKENTRY-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return hu.name, gr.name

    def _receive_and_putaway(self, qty):
        hu, gr_name = self._receive(qty)
        putaway = create_putaway(gr_name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=qty)
        return hu

    def test_find_pick_tasks_by_warehouse_order_delivery_and_hu(self):
        source_hu = self._receive_and_putaway(10)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()

        created = release_delivery_for_picking(obd.name)
        self.assertEqual(len(created), 1)
        task = frappe.get_doc("Warehouse Task", created[0])
        self.assertEqual(task.source_hu, source_hu)
        self.assertTrue(task.warehouse_order, "a Pick queue is configured, so the task should be routed to a Warehouse Order")

        # find_pick_tasks scopes by the caller's WMS Resource warehouse - run it as a resource
        # dedicated to this test's own warehouse, rather than Administrator (who may carry an
        # ambient resource from real manual RF usage on this shared test-site).
        tester_email = "pickentry-tester@example.com"
        if not frappe.db.exists("User", tester_email):
            frappe.get_doc({"doctype": "User", "email": tester_email, "first_name": "Pick Entry Tester", "send_welcome_email": 0}).insert(ignore_permissions=True)
            frappe.get_doc("User", tester_email).add_roles("WMS Picker")
        if not frappe.db.exists("WMS Resource", {"user": tester_email}):
            frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8), "user": tester_email, "warehouse": self.warehouse, "resource_type": "Operator", "active": 1}).insert(ignore_permissions=True)

        frappe.set_user(tester_email)
        try:
            by_wo = find_pick_tasks(task.warehouse_order)
            self.assertEqual([t.name for t in by_wo], [task.name])

            by_delivery = find_pick_tasks(obd.name)
            self.assertEqual([t.name for t in by_delivery], [task.name])

            by_hu = find_pick_tasks(source_hu)
            self.assertEqual([t.name for t in by_hu], [task.name])

            self.assertEqual(find_pick_tasks("NONEXISTENT-REFERENCE"), [])
        finally:
            frappe.set_user("Administrator")

    def test_find_pick_tasks_by_queue_is_scoped_to_the_resources_own_group(self):
        # Queue eligibility is a Resource Group setting a supervisor manages, never something an
        # operator self-assigns - searching "by queue" must only work for a queue the caller's
        # own group actually covers, not any queue that happens to exist in the warehouse
        # (confirmed live: WMS-TEST-PICKENTRY-PUTAWAY-QUEUE has no group at all, so it's exactly
        # the "not assigned to you" case this guards against).
        group = "WMS-TEST-PICKENTRY-GROUP"
        queue = "WMS-TEST-PICKENTRY-QUEUE-GROUPED"
        if not frappe.db.exists("WMS Resource Group", group):
            frappe.get_doc({"doctype": "WMS Resource Group", "group_code": group, "group_name": group, "warehouse": self.warehouse, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", queue):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": queue, "queue_name": queue, "warehouse": self.warehouse, "activity": "Pick", "resource_group": group, "active": 1}).insert(ignore_permissions=True)
        task = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Pick", "warehouse": self.warehouse, "product": self.item,
            "planned_quantity": 1, "stock_uom": self.uom, "destination_bin": self.stage_bin, "queue": queue,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "601", "priority": "Normal", "status": "Open"})
        task.insert(ignore_permissions=True)

        tester_email = "pickentry-grouped-tester@example.com"
        if not frappe.db.exists("User", tester_email):
            frappe.get_doc({"doctype": "User", "email": tester_email, "first_name": "Pick Entry Grouped Tester", "send_welcome_email": 0}).insert(ignore_permissions=True)
            frappe.get_doc("User", tester_email).add_roles("WMS Picker")
        if not frappe.db.exists("WMS Resource", {"user": tester_email}):
            frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8), "user": tester_email, "warehouse": self.warehouse, "resource_type": "Operator", "resource_group": group, "active": 1}).insert(ignore_permissions=True)

        frappe.set_user(tester_email)
        try:
            found = find_pick_tasks(queue)
            self.assertEqual([t.name for t in found], [task.name])
            with self.assertRaises(frappe.ValidationError):
                find_pick_tasks("WMS-TEST-PICKENTRY-PUTAWAY-QUEUE")
        finally:
            frappe.set_user("Administrator")

    def test_find_putaway_tasks_by_warehouse_order_task_and_hu(self):
        source_hu, gr_name = self._receive(10)
        putaway = create_putaway(gr_name)
        task = frappe.get_doc("Warehouse Task", putaway["warehouse_tasks"][0])
        self.assertEqual(task.source_hu, source_hu)
        self.assertTrue(task.warehouse_order, "a Putaway queue is configured, so the task should be routed to a Warehouse Order")

        # find_putaway_tasks scopes by the caller's WMS Resource warehouse - a dedicated tester,
        # same reasoning as test_find_pick_tasks_by_warehouse_order_delivery_and_hu above.
        tester_email = "putawayentry-tester@example.com"
        if not frappe.db.exists("User", tester_email):
            frappe.get_doc({"doctype": "User", "email": tester_email, "first_name": "Putaway Entry Tester", "send_welcome_email": 0}).insert(ignore_permissions=True)
            frappe.get_doc("User", tester_email).add_roles("WMS Receiver")
        if not frappe.db.exists("WMS Resource", {"user": tester_email}):
            frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8), "user": tester_email, "warehouse": self.warehouse, "resource_type": "Operator", "active": 1}).insert(ignore_permissions=True)

        frappe.set_user(tester_email)
        try:
            by_task = find_putaway_tasks(task.name)
            self.assertEqual([t.name for t in by_task], [task.name])

            by_wo = find_putaway_tasks(task.warehouse_order)
            self.assertEqual([t.name for t in by_wo], [task.name])

            by_hu = find_putaway_tasks(source_hu)
            self.assertEqual([t.name for t in by_hu], [task.name])

            self.assertEqual(find_putaway_tasks("NONEXISTENT-REFERENCE"), [])
        finally:
            frappe.set_user("Administrator")
