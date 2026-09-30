"""Regression tests for defects found by the concurrent warehouse-shift simulation
(tests/load/seed_dc.py + tests/load/simulate.py + tests/load/invariants.py).

Real concurrency can't be exercised inside one test transaction; these pin down the
deterministic half of each fix (the validation, the propagated field, the retry contract), and
the simulator + invariant checks cover the concurrent half.
"""
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.api.inbound import create_and_submit_goods_receipt
from frappe_wms.api.scanner import confirm_task, create_and_confirm_move
from frappe_wms.services import concurrency
from frappe_wms.services.replenishment import request_direct_replenishment
from frappe_wms.tests.bootstrap import empty_hu_like

WH = "LOADFIND-TEST-WH"
HU_TYPE = "LOADFIND-PALLET"


class TestLoadTestFindings(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.uom = "Nos"
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.get_single_value("Stock Settings", "enable_serial_and_batch_no_for_item"):
            frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)
        if not frappe.db.exists("WMS Warehouse", WH):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": WH, "warehouse_name": WH, "company": company,
                            "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role, hu_managed in (("GR", "Receiving", 1), ("BULK", "Storage", 1), ("PICK", "Picking", 0), ("RACK", "Storage", 1)):
            if not frappe.db.exists("Storage Type", f"{WH}-{code}"):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": WH, "storage_type_code": code, "storage_type_name": code,
                                "storage_role": role, "hu_managed": hu_managed, "capacity_check_method": "None", "active": 1}).insert(ignore_permissions=True)
        cls.recv_bin, cls.bulk_bin, cls.pick_bin, cls.rack_bin = f"{WH}-RECV", f"{WH}-BULK-1", f"{WH}-PICK-1", f"{WH}-RACK-1"
        for bin_name, code in ((cls.recv_bin, "GR"), (cls.bulk_bin, "BULK"), (cls.pick_bin, "PICK"), (cls.rack_bin, "RACK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": WH, "storage_type": f"{WH}-{code}",
                                "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", WH)
        if not wh.default_receiving_bin:
            wh.default_receiving_bin = wh.default_difference_bin = cls.recv_bin
            wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": WH, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": WH, "activity": "Putaway", "active": 1, "priority": 1,
                            "destination_storage_type": f"{WH}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", HU_TYPE):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": HU_TYPE, "hu_type_name": HU_TYPE}).insert(ignore_permissions=True)
        cls.item = cls._item("LOADFIND-ITEM")
        cls.batch_item = cls._item("LOADFIND-BATCH-ITEM", batch=True)

    @classmethod
    def _item(cls, code, batch=False):
        if not frappe.db.exists("Item", code):
            frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": code, "stock_uom": cls.uom, "is_stock_item": 1,
                            "has_batch_no": 1 if batch else 0,
                            "item_group": frappe.get_all("Item Group", filters={"is_group": 0}, limit=1, pluck="name")[0]}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", code):
            frappe.get_doc({"doctype": "WMS Product", "item": code, "stock_uom": cls.uom, "warehouse_managed": 1,
                            "batch_control": 1 if batch else 0, "active": 1}).insert(ignore_permissions=True)
        return code

    def _delivery(self, item, qty):
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": WH,
                              "supplier": self.supplier, "receiving_bin": self.recv_bin,
                              "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        return ind

    def _receive(self, ind, qty, batch_no=None):
        line = {"inbound_delivery_item": ind.items[0].name, "item": ind.items[0].item, "quantity": qty, "stock_uom": self.uom,
                "handling_unit": frappe.generate_hash(length=10), "stock_type": "AVAILABLE", "hu_type": HU_TYPE, "batch_no": batch_no}
        return create_and_submit_goods_receipt(ind.name, [line]), line["handling_unit"]

    def _stock(self, item, qty, bin_name):
        # Receive and put away a whole pallet of `item`, then relocate it to `bin_name` if needed.
        ind = self._delivery(item, qty)
        result, hu = self._receive(ind, qty)
        task = result["warehouse_tasks"][0]
        confirm_task(task, confirmed_quantity=qty)
        landed = frappe.db.get_value("Warehouse Task", task, "destination_bin")
        if landed != bin_name:
            create_and_confirm_move(warehouse=WH, product=item, quantity=qty, stock_uom=self.uom, stock_type="AVAILABLE",
                                    source_bin=landed, source_hu=hu, destination_bin=bin_name, destination_hu=hu)
        return hu

    # --- batch/serial on Warehouse Request ------------------------------------------------------

    def test_batch_controlled_receipt_can_be_put_away(self):
        # Warehouse Request had no batch_no, so the putaway task was created without one and its
        # confirmation looked for a batch-less balance that never exists: "Insufficient stock".
        ind = self._delivery(self.batch_item, 12)
        result, hu = self._receive(ind, 12, batch_no=f"LF-B-{frappe.generate_hash(length=6)}")
        batch_no = frappe.db.get_value("WMS Stock Balance", {"handling_unit": hu}, "batch_no")
        request = frappe.get_doc("Warehouse Request", result["warehouse_requests"][0])
        self.assertEqual(request.batch_no, batch_no)
        task_name = result["warehouse_tasks"][0]
        self.assertEqual(frappe.db.get_value("Warehouse Task", task_name, "batch_no"), batch_no)
        confirm_task(task_name, confirmed_quantity=12)
        self.assertEqual(flt(frappe.db.get_value("WMS Stock Balance", {"handling_unit": hu, "storage_bin": self.bulk_bin}, "quantity")), 12)

    def test_replenishment_carries_the_source_batch(self):
        ind = self._delivery(self.batch_item, 20)
        result, hu = self._receive(ind, 20, batch_no=f"LF-R-{frappe.generate_hash(length=6)}")
        confirm_task(result["warehouse_tasks"][0], confirmed_quantity=20)
        batch_no = frappe.db.get_value("WMS Stock Balance", {"handling_unit": hu, "storage_bin": self.bulk_bin}, "batch_no")
        created = request_direct_replenishment(WH, self.batch_item, self.pick_bin, "AVAILABLE", 5, f"{WH}-BULK")
        task_name = created["task"]
        self.assertEqual(frappe.db.get_value("Warehouse Task", task_name, "batch_no"), batch_no)
        # PICK isn't HU-managed, so the 5 go in loose and the pallet (15 left) stays in BULK.
        confirm_task(task_name, confirmed_quantity=5)
        self.assertEqual(flt(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.pick_bin, "product": self.batch_item, "batch_no": batch_no}, "quantity")), 5)
        self.assertFalse(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.pick_bin, "handling_unit": hu}, "name"))
        self.assertEqual(frappe.db.get_value("Handling Unit", hu, "current_bin"), self.bulk_bin)

    # --- receipt quantity validation and progress -------------------------------------------------

    def test_receipt_beyond_remaining_quantity_is_refused(self):
        ind = self._delivery(self.item, 10)
        self._receive(ind, 6)
        with self.assertRaises(frappe.ValidationError):
            self._receive(ind, 5)  # only 4 left - the RF app checks this, the server never did
        self._receive(ind, 4)
        row = frappe.get_doc("Inbound Delivery", ind.name).items[0]
        self.assertEqual(flt(row.received_quantity), 10)
        self.assertEqual(row.status, "Received")

    def test_cancelling_a_goods_receipt_reopens_the_delivery_line(self):
        ind = self._delivery(self.item, 8)
        result, _hu = self._receive(ind, 8)
        self.assertEqual(frappe.db.get_value("Inbound Delivery", ind.name, "receipt_status"), "Fully Received")
        for task in frappe.get_all("Warehouse Task", filters={"warehouse_request": ["in", result["warehouse_requests"]]}, pluck="name"):
            frappe.db.set_value("Warehouse Task", task, {"status": "Cancelled", "docstatus": 2})
        gr = frappe.get_doc("Goods Receipt", result["goods_receipt"])
        gr.flags.ignore_permissions = True
        gr.cancel()
        row = frappe.get_doc("Inbound Delivery", ind.name).items[0]
        self.assertEqual(flt(row.received_quantity), 0)
        self.assertEqual(row.status, "Open")
        self.assertEqual(frappe.db.get_value("Inbound Delivery", ind.name, "receipt_status"), "Not Received")

    # --- quality inspection follows its HU -------------------------------------------------------

    def test_inspection_can_be_completed_after_putaway(self):
        # The inspection is raised at Goods Receipt, pointing at the receiving bin; putaway then
        # moves the QUALITY pallet on. Completing it used to fail with "Insufficient stock".
        from frappe_wms.services.quality import complete_inspection
        ind = self._delivery(self.item, 6)
        line = {"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 6, "stock_uom": self.uom,
                "handling_unit": frappe.generate_hash(length=10), "stock_type": "QUALITY", "hu_type": HU_TYPE}
        result = create_and_submit_goods_receipt(ind.name, [line])
        inspection = frappe.get_all("WMS Quality Inspection", filters={"goods_receipt": result["goods_receipt"]}, pluck="name")[0]
        confirm_task(result["warehouse_tasks"][0], confirmed_quantity=6)
        self.assertNotEqual(frappe.db.get_value("Handling Unit", line["handling_unit"], "current_bin"), self.recv_bin)
        complete_inspection(inspection, passed_quantity=5, failed_quantity=1)
        where = frappe.db.get_value("Handling Unit", line["handling_unit"], "current_bin")
        self.assertEqual(flt(frappe.db.get_value("WMS Stock Balance", {"handling_unit": line["handling_unit"], "storage_bin": where, "stock_type": "AVAILABLE"}, "quantity")), 5)

    # --- allocation status ----------------------------------------------------------------------

    def test_delivery_with_no_stock_stays_not_allocated(self):
        from frappe_wms.services.allocation import allocate_delivery
        from frappe_wms.services.picking import release_delivery_for_picking
        item = self._item("LOADFIND-NO-STOCK")
        customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": WH,
                              "customer": customer, "delivery_date": frappe.utils.nowdate(), "staging_bin": self.rack_bin,
                              "items": [{"line_number": 1, "item": item, "requested_quantity": 3, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", obd.name, "allocation_status"), "Not Allocated")
        with self.assertRaisesRegex(frappe.ValidationError, "No stock could be allocated"):
            release_delivery_for_picking(obd.name)

    # --- partial quantity out of an HU ----------------------------------------------------------

    def test_partial_move_into_the_source_hu_is_refused(self):
        hu = self._stock(self.item, 10, self.bulk_bin)
        with self.assertRaises(frappe.ValidationError):
            create_and_confirm_move(warehouse=WH, product=self.item, quantity=4, stock_uom=self.uom, stock_type="AVAILABLE",
                                    source_bin=self.bulk_bin, source_hu=hu, destination_bin=self.rack_bin, destination_hu=hu)
        tote = empty_hu_like(hu, self.rack_bin)
        create_and_confirm_move(warehouse=WH, product=self.item, quantity=4, stock_uom=self.uom, stock_type="AVAILABLE",
                                source_bin=self.bulk_bin, source_hu=hu, destination_bin=self.rack_bin, destination_hu=tote)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu, "current_bin"), self.bulk_bin)
        self.assertEqual(flt(frappe.db.get_value("WMS Stock Balance", {"handling_unit": tote, "storage_bin": self.rack_bin}, "quantity")), 4)

    def test_whole_hu_still_travels_with_its_stock(self):
        hu = self._stock(self.item, 9, self.bulk_bin)
        create_and_confirm_move(warehouse=WH, product=self.item, quantity=9, stock_uom=self.uom, stock_type="AVAILABLE",
                                source_bin=self.bulk_bin, source_hu=hu, destination_bin=self.rack_bin, destination_hu=hu)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu, "current_bin"), self.rack_bin)
        self.assertEqual(flt(frappe.db.get_value("WMS Stock Balance", {"handling_unit": hu, "storage_bin": self.rack_bin}, "quantity")), 9)


class TestRetryOnDeadlock(IntegrationTestCase):
    def _run(self, fn):
        # retry_on_deadlock deliberately does nothing under tests (a rollback would take the test's
        # own fixtures with it) - switch that off and stub the rollback to observe the contract.
        with patch.dict(frappe.flags, {"in_test": False}), patch.object(frappe.db, "rollback") as rollback, \
                patch.object(concurrency.time, "sleep"):
            return fn(), rollback

    def test_whole_call_is_rerun_after_a_deadlock(self):
        calls = []

        @concurrency.retry_on_deadlock
        def endpoint():
            calls.append(1)
            if len(calls) < 3:
                raise frappe.QueryDeadlockError("1213")
            return "done"

        result, rollback = self._run(endpoint)
        self.assertEqual(result, "done")
        self.assertEqual(len(calls), 3)
        self.assertEqual(rollback.call_count, 2)

    def test_gives_up_after_the_last_attempt(self):
        @concurrency.retry_on_deadlock
        def endpoint():
            raise frappe.QueryDeadlockError("1213")

        with self.assertRaises(frappe.QueryDeadlockError):
            self._run(endpoint)

    def test_other_errors_are_not_retried(self):
        calls = []

        @concurrency.retry_on_deadlock
        def endpoint():
            calls.append(1)
            frappe.throw("business rule")

        with self.assertRaises(frappe.ValidationError):
            self._run(endpoint)
        self.assertEqual(len(calls), 1)

    def test_only_the_outermost_call_retries(self):
        inner_calls = []

        @concurrency.retry_on_deadlock
        def inner():
            inner_calls.append(1)
            if len(inner_calls) == 1:
                raise frappe.QueryDeadlockError("1213")
            return "ok"

        @concurrency.retry_on_deadlock
        def outer():
            return inner()

        result, rollback = self._run(outer)
        self.assertEqual(result, "ok")
        # the inner call re-raised to the outer one, which retried the whole thing exactly once
        self.assertEqual(len(inner_calls), 2)
        self.assertEqual(rollback.call_count, 1)


class TestRolePermissions(IntegrationTestCase):
    def _user(self, email, roles):
        if not frappe.db.exists("User", email):
            user = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0,
                                   "user_type": "System User", "roles": [{"role": r} for r in roles]})
            user.insert(ignore_permissions=True)
        return email

    def test_receiver_and_supervisor_can_work_inbound_deliveries(self):
        receiver = self._user("loadfind.receiver@example.test", ["WMS Receiver"])
        supervisor = self._user("loadfind.supervisor@example.test", ["WMS Supervisor"])
        self.assertTrue(frappe.has_permission("Inbound Delivery", "read", user=receiver))
        self.assertTrue(frappe.has_permission("Inbound Delivery", "submit", user=supervisor))

    def test_loader_may_depart_a_shipment(self):
        from frappe_wms.services.shipping import depart_shipment
        loader = self._user("loadfind.loader@example.test", ["WMS Loader"])
        frappe.set_user(loader)
        try:
            # A missing shipment gets past the role gate and fails on lookup instead - the point is
            # that it is no longer "not permitted to perform this warehouse operation".
            with self.assertRaises(frappe.DoesNotExistError):
                depart_shipment("LOADFIND-NO-SUCH-SHIPMENT")
        finally:
            frappe.set_user("Administrator")
