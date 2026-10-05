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
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
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

    def _setting(self, **values):
        for k, v in values.items(): frappe.db.set_single_value("WMS Settings", k, v)
        self.addCleanup(lambda: [frappe.db.set_single_value("WMS Settings", k, 0) for k in values])
        frappe.clear_cache()

    def _stock(self, bin_name, qty):
        post_entries([{"warehouse": self.warehouse, "product": self.item, "storage_bin": bin_name, "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701"}],
            "Storage Bin", bin_name, f"test-replen-stock:{frappe.generate_hash(length=8)}")

    def test_a_confirmed_pick_below_the_minimum_raises_the_replenishment_when_enabled(self):
        from frappe_wms.api.scanner import confirm_task
        self._seed_bulk_stock(self.item, 50)
        self._stock(self.pick_bin, 8)
        task = self._make_pick_task(self.pick_bin, planned_quantity=5)
        confirm_task(task.name, confirmed_quantity=5)
        self.assertFalse(frappe.db.exists("Warehouse Request", {"reference_doctype": "Replenishment Rule", "destination_bin": self.pick_bin, "status": ["!=", "Cancelled"]}), "off by default: only the hourly scan")
        self._setting(replenish_on_removal=1)
        self._stock(self.pick_bin, 5)  # 8 again
        confirm_task(self._make_pick_task(self.pick_bin, planned_quantity=5).name, confirmed_quantity=5)
        self.assertTrue(frappe.db.exists("Warehouse Request", {"reference_doctype": "Replenishment Rule", "destination_bin": self.pick_bin, "status": ["!=", "Cancelled"]}))

    def test_a_pick_that_empties_the_bin_asks_for_a_count_when_enabled(self):
        from frappe_wms.api.scanner import confirm_task
        self._setting(zero_stock_check_on_pick=1)
        self._stock(self.no_rule_pick_bin, 6)
        confirm_task(self._make_pick_task(self.no_rule_pick_bin, planned_quantity=3).name, confirmed_quantity=3)
        self.assertFalse(frappe.db.exists("WMS Physical Inventory Count", {"storage_bin": self.no_rule_pick_bin}), "stock left: no count")
        confirm_task(self._make_pick_task(self.no_rule_pick_bin, planned_quantity=3).name, confirmed_quantity=3)
        counts = frappe.get_all("WMS Physical Inventory Count", filters={"storage_bin": self.no_rule_pick_bin}, fields=["status"])
        self.assertEqual([c.status for c in counts], ["Draft"])
        self._stock(self.no_rule_pick_bin, 1)
        confirm_task(self._make_pick_task(self.no_rule_pick_bin, planned_quantity=1).name, confirmed_quantity=1)
        self.assertEqual(frappe.db.count("WMS Physical Inventory Count", {"storage_bin": self.no_rule_pick_bin}), 1, "one open count per bin")

    def test_blocking_a_bin_needs_a_reason_when_the_setting_is_on(self):
        reason = frappe.db.get_value("WMS Block Reason", "TEST-DAMAGED") or frappe.get_doc({"doctype": "WMS Block Reason", "reason_code": "TEST-DAMAGED", "reason_name": "Damaged rack", "applies_to": "Bin"}).insert(ignore_permissions=True).name
        hu_only = frappe.db.get_value("WMS Block Reason", "TEST-HUONLY") or frappe.get_doc({"doctype": "WMS Block Reason", "reason_code": "TEST-HUONLY", "reason_name": "Broken pallet", "applies_to": "Handling Unit"}).insert(ignore_permissions=True).name
        def block(**values):
            doc = frappe.get_doc("Storage Bin", self.pick_bin)
            doc.update({"putaway_blocked": 1, "block_reason": None, **values})
            doc.save(ignore_permissions=True)
        block()  # setting off: no reason needed
        self._setting(require_block_reason=1)
        with self.assertRaises(frappe.ValidationError): block()
        with self.assertRaises(frappe.ValidationError): block(block_reason=hu_only)
        block(block_reason=reason)

    def _code(self, code, **values):
        return frappe.db.get_value("WMS Exception Code", code) or frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": code, "exception_name": code, "category": "Task", "active": 1, **values}).insert(ignore_permissions=True).name

    def test_change_bin_exception_moves_the_destination_of_a_putaway_and_the_source_of_a_move(self):
        if not frappe.db.exists("Handling Unit Type", "TEST-EXC-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-EXC-PALLET", "hu_type_name": "Exc Pallet"}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-EXC-PALLET", "warehouse": self.warehouse, "current_bin": self.bulk_bin, "status": "Open"}).insert(ignore_permissions=True)
        code = self._code("TEST-CHBIN", system_action="Change Bin")
        putaway = frappe.get_doc({"doctype": "Warehouse Task", "task_type": "Putaway", "warehouse": self.warehouse, "product": self.item, "planned_quantity": 3, "stock_uom": self.uom, "source_bin": self.bulk_bin,
            "source_hu": hu.name, "destination_bin": self.pick_bin, "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "201", "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError): raise_exception(putaway.name, code, remarks="rack full")  # needs the new bin
        result = raise_exception(putaway.name, code, remarks="rack full", new_bin=self.no_rule_pick_bin)
        self.assertEqual((result["bin_changed"], frappe.db.get_value("Warehouse Task", putaway.name, ["destination_bin", "status"])), (self.no_rule_pick_bin, (self.no_rule_pick_bin, "Open")))
        self._stock(self.no_rule_pick_bin, 10)
        move = self._make_pick_task(self.pick_bin, planned_quantity=4)
        raise_exception(move.name, code, new_bin=self.no_rule_pick_bin)
        self.assertEqual(frappe.db.get_value("Warehouse Task", move.name, "source_bin"), self.no_rule_pick_bin)
        empty = f"{self.warehouse}-EMPTYC"
        if not frappe.db.exists("Storage Bin", empty): frappe.get_doc({"doctype": "Storage Bin", "bin_code": empty, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-PICK", "active": 1, "sequence": 5}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError): raise_exception(self._make_pick_task(self.pick_bin, planned_quantity=4).name, code, new_bin=empty)  # holds no stock

    def test_split_task_exception_carves_a_new_task_off_to_another_bin(self):
        code = self._code("TEST-SPLT", system_action="Split Task")
        task = self._make_pick_task(self.pick_bin, planned_quantity=10)
        with self.assertRaises(frappe.ValidationError): raise_exception(task.name, code, split_quantity=10)
        result = raise_exception(task.name, code, remarks="half the pallet", split_quantity=4, new_bin=self.no_rule_pick_bin)
        self.assertEqual(frappe.db.get_value("Warehouse Task", task.name, "planned_quantity"), 6)
        new = frappe.get_doc("Warehouse Task", result["split_task"])
        self.assertEqual((new.planned_quantity, new.destination_bin, new.product), (4, self.no_rule_pick_bin, self.item))

    def test_skip_task_exception_sends_the_task_to_the_back_and_codes_are_offered_by_context(self):
        from frappe_wms.api.scanner import list_exception_codes
        code = self._code("TEST-NEXT", system_action="Skip Task")
        first, second = self._make_pick_task(self.pick_bin), self._make_pick_task(self.pick_bin)
        frappe.db.set_value("Warehouse Task", first.name, {"warehouse_order": None, "sequence": 1})
        raise_exception(first.name, code)
        self.assertEqual(frappe.db.get_value("Warehouse Task", first.name, "status"), "Open", "skipping blocks nothing")
        packing = self._code("TEST-PACKONLY", business_context="Packing")
        for c in (code, packing):
            doc = frappe.get_doc("WMS Exception Code", c)
            if not doc.allowed_task_types: doc.append("allowed_task_types", {"task_type": "Pick"}); doc.save(ignore_permissions=True)
        names = [c.name for c in list_exception_codes("Pick")]
        self.assertNotIn(packing, names, "a packing-only code is not offered on the RF task screen")
        self.assertIn(code, names)
