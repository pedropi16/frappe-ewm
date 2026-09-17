import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks, post_goods_issue_for_delivery, create_and_submit_goods_issue
from frappe_wms.api.scanner import confirm_task, reverse_task
from frappe_wms.api.monitor import get_delivery_execution_status
from frappe_wms.services.shipping import create_shipment, confirm_hu_loaded


class TestOutboundDeliveryLifecycle(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "OBDLIFE-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "OBDLIFE-PALLET", "hu_type_name": "OBD Lifecycle Pallet"}).insert(ignore_permissions=True)

    def _new_scenario(self):
        # A fresh warehouse per test: confirming a pick moves stock into the staging bin as a
        # brand new, unreserved WMS Stock Balance row (nothing marks staged stock as "reserved
        # for this delivery" once it arrives) - sharing one warehouse across test methods in this
        # class (this test-site never rolls back between test methods) would let a later test's
        # FIFO allocation pick up an earlier test's already-staged stock. A real allocation-engine
        # quirk, but not one this test is about.
        warehouse = f"WMS-TEST-OBDLIFE-{frappe.generate_hash(length=6).upper()}"
        recv_bin, bulk_bin, stage_bin, door_bin = f"{warehouse}-RECV", f"{warehouse}-BULK", f"{warehouse}-STAGE", f"{warehouse}-DOOR"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": warehouse, "warehouse_name": warehouse, "company": self.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "DOOR", "storage_type_name": "DOOR", "storage_role": "Door", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((recv_bin, f"{warehouse}-GR"), (bulk_bin, f"{warehouse}-BULK"), (stage_bin, f"{warehouse}-GR"), (door_bin, f"{warehouse}-DOOR")):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", warehouse)
        wh.default_receiving_bin = recv_bin
        wh.default_shipping_bin = stage_bin
        wh.default_difference_bin = recv_bin
        wh.save(ignore_permissions=True)
        frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WMS Route", "route_code": f"{warehouse}-ROUTE", "route_name": f"{warehouse}-ROUTE",
            "origin_warehouse": warehouse, "default_staging_bin": stage_bin, "default_door": door_bin, "active": 1}).insert(ignore_permissions=True)
        return frappe._dict(warehouse=warehouse, recv_bin=recv_bin, bulk_bin=bulk_bin, stage_bin=stage_bin, door_bin=door_bin)

    def _load_hu(self, scenario, delivery_name, hu_name):
        # Mirrors the real Ready-to-Load -> loading flow: Goods Issue requires the HU to have
        # actually been loaded onto a Shipment, not merely staged after picking.
        shipment_name = create_shipment(scenario.warehouse, [delivery_name])
        confirm_hu_loaded(shipment_name, hu_name)
        return shipment_name

    def _receive_and_putaway(self, scenario, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "OBDLIFE-PALLET", "warehouse": scenario.warehouse, "current_bin": scenario.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse, "supplier": self.supplier, "receiving_bin": scenario.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": scenario.warehouse, "receiving_bin": scenario.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        task = create_putaway(gr.name)["warehouse_tasks"][0]
        confirm_task(task, confirmed_quantity=qty)
        return hu

    def _make_delivery(self, scenario, qty):
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": scenario.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        return obd

    def test_allocate_and_pick_require_a_submitted_delivery(self):
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 10)
        obd = self._make_delivery(scenario, 3)
        with self.assertRaises(frappe.ValidationError):
            allocate_delivery(obd.name)

        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        self.assertEqual(len(pick_tasks), 1)

    def test_cancel_blocked_while_goods_issue_is_posted(self):
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 6)
        obd = self._make_delivery(scenario, 6)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=6)
        hu_name = frappe.db.get_value("Warehouse Task", pick_tasks[0], "destination_hu")
        # This test is about cancel-blocking once Goods Issue is posted, not about the physical
        # loading flow (that's covered by test_post_goods_issue_for_delivery_requires_the_hu_to_be_loaded
        # and the RF parity ship test) - flip the HU straight to Loaded in place, and the staging
        # bin to a Door bin in place, so the stock stays put and the later reverse_task below has
        # something to undo (a real load would relocate it, which reverse_task can't unwind).
        frappe.db.set_value("Storage Bin", scenario.stage_bin, "storage_type", f"{scenario.warehouse}-DOOR")
        frappe.db.set_value("Handling Unit", hu_name, "status", "Loaded")

        result = post_goods_issue_for_delivery(obd.name)
        gi = frappe.get_doc("Goods Issue", result["goods_issue"])
        self.assertEqual(gi.status, "Posted")

        obd.reload()
        with self.assertRaises(frappe.ValidationError):
            obd.cancel()

        gi.cancel()

        # Reversing the Goods Issue only undoes the shipment - the Pick task itself is still
        # Confirmed, so before_cancel still (correctly) blocks the delivery until that's reversed
        # too, exactly like the "picking already started" rule below.
        obd.reload()
        with self.assertRaises(frappe.ValidationError):
            obd.cancel()

        reverse_task(pick_tasks[0])
        obd.reload()
        obd.cancel()
        self.assertEqual(obd.docstatus, 2)

    def test_cancel_releases_allocations_and_cancels_open_tasks(self):
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 7)
        obd = self._make_delivery(scenario, 4)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)

        allocation = frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name}, fields=["name", "stock_balance"])[0]
        balance_before = frappe.db.get_value("WMS Stock Balance", allocation.stock_balance, "allocated_quantity")
        self.assertGreater(balance_before, 0)

        obd.reload()
        obd.cancel()

        self.assertEqual(frappe.db.get_value("Stock Allocation", allocation.name, "status"), "Cancelled")
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", allocation.stock_balance, "allocated_quantity"), 0)
        self.assertEqual(frappe.db.get_value("Warehouse Task", pick_tasks[0], "status"), "Cancelled")
        self.assertEqual(frappe.db.get_value("Warehouse Task", pick_tasks[0], "docstatus"), 2)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", obd.name, "status"), "Cancelled")

    def test_cancel_blocked_once_picking_has_started(self):
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 5)
        obd = self._make_delivery(scenario, 5)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=5)

        obd.reload()
        with self.assertRaises(frappe.ValidationError):
            obd.cancel()

    def test_post_goods_issue_for_delivery_requires_a_staged_hu(self):
        scenario = self._new_scenario()
        obd = self._make_delivery(scenario, 1)
        obd.submit()
        with self.assertRaises(frappe.ValidationError):
            post_goods_issue_for_delivery(obd.name)

    def test_post_goods_issue_for_delivery_requires_the_hu_to_be_loaded(self):
        # Picking only stages the HU (see task.py's _move_hu_if_complete); Goods Issue must not
        # be postable until it has actually been loaded onto a Shipment.
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 4)
        obd = self._make_delivery(scenario, 4)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=4)
        hu_name = frappe.db.get_value("Warehouse Task", pick_tasks[0], "destination_hu")
        self.assertEqual(frappe.db.get_value("Handling Unit", hu_name, "status"), "Staged")

        with self.assertRaises(frappe.ValidationError):
            post_goods_issue_for_delivery(obd.name)

        # Loading this last HU finishes the shipment, which auto-posts Goods Issue for the
        # delivery on its own (see shipping.confirm_hu_loaded) - nothing left to post manually.
        self._load_hu(scenario, obd.name, hu_name)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu_name, "status"), "Shipped")
        self.assertEqual(frappe.db.get_value("Outbound Delivery", obd.name, "goods_issue_status"), "Posted")
        gi_name = frappe.get_all("Goods Issue", filters={"outbound_delivery": obd.name}, pluck="name")[0]
        self.assertEqual(frappe.db.get_value("Goods Issue", gi_name, "status"), "Posted")

    def test_route_default_door_must_be_a_door_bin(self):
        scenario = self._new_scenario()
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "WMS Route", "route_code": f"{scenario.warehouse}-BADROUTE", "route_name": f"{scenario.warehouse}-BADROUTE",
                "origin_warehouse": scenario.warehouse, "default_staging_bin": scenario.stage_bin,
                "default_door": scenario.stage_bin, "active": 1}).insert(ignore_permissions=True)

    def test_shipment_door_must_be_a_door_bin_even_if_set_manually(self):
        scenario = self._new_scenario()
        route = f"{scenario.warehouse}-ROUTE"
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "WMS Shipment", "shipment_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse,
                "route": route, "status": "Planned", "door": scenario.stage_bin}).insert(ignore_permissions=True)

    def test_post_goods_issue_rejects_a_loaded_hu_outside_a_door_bin(self):
        # Even if something (a manual status edit, a future bug) marks the HU Loaded without it
        # actually sitting in a Door bin, Goods Issue must still refuse to post - the door is
        # what makes the posting legitimate, not the status flag alone.
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 4)
        obd = self._make_delivery(scenario, 4)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=4)
        hu_name = frappe.db.get_value("Warehouse Task", pick_tasks[0], "destination_hu")
        self.assertEqual(frappe.db.get_value("Handling Unit", hu_name, "current_bin"), scenario.stage_bin)
        frappe.db.set_value("Handling Unit", hu_name, "status", "Loaded")

        with self.assertRaises(frappe.ValidationError):
            create_and_submit_goods_issue(obd.name, [
                {"outbound_delivery_item": obd.items[0].name, "item": self.item, "quantity": 4, "stock_uom": self.uom, "handling_unit": hu_name, "stock_type": "AVAILABLE"},
            ])

    def test_get_delivery_execution_status_reports_the_full_picture(self):
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 8)
        obd = self._make_delivery(scenario, 8)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=8)
        hu_name = frappe.db.get_value("Warehouse Task", pick_tasks[0], "destination_hu")
        # Loading finishes the shipment, which auto-posts Goods Issue on its own.
        self._load_hu(scenario, obd.name, hu_name)
        gi_name = frappe.get_all("Goods Issue", filters={"outbound_delivery": obd.name}, pluck="name")[0]

        status = get_delivery_execution_status(obd.name)
        self.assertEqual(status["delivery"]["name"], obd.name)
        self.assertIn(pick_tasks[0], [t.name for t in status["tasks"]])
        self.assertTrue(status["warehouse_orders"] == [] or isinstance(status["warehouse_orders"], list))
        self.assertIn(gi_name, [g.name for g in status["goods_issues"]])

    def test_goods_issue_auto_posts_only_once_every_hu_on_the_shipment_is_loaded(self):
        # A delivery with two lines, each fulfilled from its own HU (both staged onto the same
        # Shipment), must not get its Goods Issue posted until the *last* HU is loaded - loading
        # one of two is still a partial shipment.
        scenario = self._new_scenario()
        self._receive_and_putaway(scenario, 3)
        self._receive_and_putaway(scenario, 3)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse,
            "customer": self.customer, "delivery_date": nowdate(), "staging_bin": scenario.stage_bin,
            "items": [
                {"line_number": 1, "item": self.item, "requested_quantity": 3, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"},
                {"line_number": 2, "item": self.item, "requested_quantity": 3, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"},
            ]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        self.assertEqual(len(pick_tasks), 2)
        for task in pick_tasks:
            confirmed_quantity = frappe.db.get_value("Warehouse Task", task, "planned_quantity")
            confirm_task(task, confirmed_quantity=confirmed_quantity)
        hu_names = [frappe.db.get_value("Warehouse Task", t, "destination_hu") for t in pick_tasks]

        shipment_name = create_shipment(scenario.warehouse, [obd.name])
        confirm_hu_loaded(shipment_name, hu_names[0])
        self.assertFalse(frappe.db.exists("Goods Issue", {"outbound_delivery": obd.name}))
        self.assertEqual(frappe.db.get_value("Outbound Delivery", obd.name, "goods_issue_status"), "Not Posted")

        confirm_hu_loaded(shipment_name, hu_names[1])
        gi_name = frappe.get_all("Goods Issue", filters={"outbound_delivery": obd.name}, pluck="name")
        self.assertEqual(len(gi_name), 1)
        self.assertEqual(frappe.db.get_value("Goods Issue", gi_name[0], "status"), "Posted")
        self.assertEqual(frappe.db.get_value("Outbound Delivery", obd.name, "goods_issue_status"), "Posted")
