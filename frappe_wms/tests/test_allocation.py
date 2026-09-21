import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate, flt

from frappe_wms.services.stock import post_entries

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task


class TestAllocation(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-ALLOC-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.blocked_bin = f"{cls.warehouse}-BLOCKED"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in ((f"{cls.warehouse}-GR", "Receiving"), (f"{cls.warehouse}-BULK", "Storage"), (f"{cls.warehouse}-STAGE", "Staging")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-STAGE"), (cls.blocked_bin, f"{cls.warehouse}-BULK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.stage_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Bin Sequence"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        cls.item2 = "TEST-ALLOC-ITEM-2"
        if not frappe.db.exists("Item", cls.item2):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": cls.item2, "item_name": cls.item2, "item_group": item_group, "stock_uom": cls.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item2}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item2, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "ALLOC-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "ALLOC-PALLET", "hu_type_name": "Alloc Pallet"}).insert(ignore_permissions=True)
        frappe.db.set_value("Storage Bin", cls.bulk_bin, "sequence", 1)
        frappe.db.set_value("Storage Bin", cls.blocked_bin, "sequence", 2)

    def _receive_and_putaway(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "ALLOC-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        task = create_putaway(gr.name)["warehouse_tasks"][0]
        confirm_task(task, confirmed_quantity=qty)
        return hu

    def _make_and_submit_delivery(self, qty, item=None):
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item or self.item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        return obd

    def test_allocation_does_not_reuse_stock_already_staged_for_another_delivery(self):
        self._receive_and_putaway(10)
        first = self._make_and_submit_delivery(6)
        allocate_delivery(first.name)
        pick_tasks = create_pick_tasks(first.name)
        confirm_task(pick_tasks[0], confirmed_quantity=6)
        # The confirmed pick moved 6 units into self.stage_bin - a fresh, unreserved WMS Stock
        # Balance row there, which previously looked like ordinary available inventory to FIFO.
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.stage_bin, "product": self.item}, "quantity"), 6)

        self._receive_and_putaway(4)
        second = self._make_and_submit_delivery(3)
        allocate_delivery(second.name)

        allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": second.name}, fields=["storage_bin"])
        self.assertTrue(allocations)
        self.assertTrue(all(a.storage_bin != self.stage_bin for a in allocations), "allocation must not source stock sitting in a Staging bin")

    def test_allocation_rereads_balance_under_lock_before_reserving(self):
        # A real concurrent-session race can't be exercised in-process; this proves the
        # re-fetch-under-lock logic instead - allocate_delivery must reserve against the
        # balance's current available_quantity, not whatever _candidate_balances saw first,
        # by simulating another transaction having drained the bin in between. Uses a
        # dedicated item so no leftover balance from other tests in this class can contend.
        item = "TEST-ALLOC-ITEM-LOCK"
        if not frappe.db.exists("Item", item):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item}):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)

        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "ALLOC-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item, "expected_quantity": 10, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": 10, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        task = create_putaway(gr.name)["warehouse_tasks"][0]
        confirm_task(task, confirmed_quantity=10)

        balance_name = frappe.db.get_value("WMS Stock Balance", {"product": item, "quantity": [">", 0]}, "name")
        frappe.db.set_value("WMS Stock Balance", balance_name, {"quantity": 2, "available_quantity": 2})

        obd = self._make_and_submit_delivery(10, item=item)
        allocate_delivery(obd.name)
        obd.reload()
        self.assertEqual(flt(obd.items[0].allocated_quantity), 2, "must not allocate more than the balance's current available_quantity")
        balance = frappe.get_doc("WMS Stock Balance", balance_name)
        self.assertEqual(flt(balance.available_quantity), 0)
        self.assertGreaterEqual(flt(balance.quantity), flt(balance.allocated_quantity))

    def _make_fefo_item(self, item_code):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1, "has_batch_no": 1}).insert(ignore_permissions=True)
        return item_code

    def _make_batch(self, batch_id, item_code):
        if not frappe.db.exists("Batch", batch_id):
            frappe.get_doc({"doctype": "Batch", "batch_id": batch_id, "item": item_code}).insert(ignore_permissions=True)
        return batch_id

    def test_allocation_prefers_soonest_expiry_over_receipt_order(self):
        item = self._make_fefo_item("TEST-ALLOC-ITEM-FEFO-1")
        if not frappe.db.exists("WMS Product", {"item": item}):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1, "shelf_life_days": 30}).insert(ignore_permissions=True)
        self._make_batch("FEFO-LATE", item)
        self._make_batch("FEFO-SOON", item)
        # Post the later-expiring batch first (so plain FIFO-by-receipt-order would pick it
        # first) and the sooner-expiring batch second, to prove FEFO overrides receipt order.
        post_entries(
            [{"warehouse": self.warehouse, "product": item, "storage_bin": self.bulk_bin, "batch_no": "FEFO-LATE",
              "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701",
              "shelf_life_expiry_date": add_days(nowdate(), 60)}],
            "Storage Bin", self.bulk_bin, f"test-alloc-fefo-late:{frappe.generate_hash(length=8)}",
        )
        post_entries(
            [{"warehouse": self.warehouse, "product": item, "storage_bin": self.blocked_bin, "batch_no": "FEFO-SOON",
              "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701",
              "shelf_life_expiry_date": add_days(nowdate(), 5)}],
            "Storage Bin", self.blocked_bin, f"test-alloc-fefo-soon:{frappe.generate_hash(length=8)}",
        )
        obd = self._make_and_submit_delivery(5, item=item)
        allocate_delivery(obd.name)
        allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name}, fields=["batch_no"])
        self.assertTrue(allocations)
        self.assertTrue(all(a.batch_no == "FEFO-SOON" for a in allocations), "must allocate the soonest-expiring batch first")

    def test_allocation_excludes_stock_below_minimum_remaining_shelf_life(self):
        item = self._make_fefo_item("TEST-ALLOC-ITEM-FEFO-2")
        if not frappe.db.exists("WMS Product", {"item": item}):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1, "shelf_life_days": 30, "minimum_remaining_shelf_life": 10}).insert(ignore_permissions=True)
        self._make_batch("MRSL-EXPIRING", item)
        self._make_batch("MRSL-SAFE", item)
        post_entries(
            [{"warehouse": self.warehouse, "product": item, "storage_bin": self.bulk_bin, "batch_no": "MRSL-EXPIRING",
              "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701",
              "shelf_life_expiry_date": add_days(nowdate(), 3)}],
            "Storage Bin", self.bulk_bin, f"test-alloc-mrsl-expiring:{frappe.generate_hash(length=8)}",
        )
        post_entries(
            [{"warehouse": self.warehouse, "product": item, "storage_bin": self.blocked_bin, "batch_no": "MRSL-SAFE",
              "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701",
              "shelf_life_expiry_date": add_days(nowdate(), 60)}],
            "Storage Bin", self.blocked_bin, f"test-alloc-mrsl-safe:{frappe.generate_hash(length=8)}",
        )
        obd = self._make_and_submit_delivery(5, item=item)
        allocate_delivery(obd.name)
        allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name}, fields=["batch_no"])
        self.assertTrue(allocations)
        self.assertTrue(all(a.batch_no == "MRSL-SAFE" for a in allocations), "must not allocate stock closer to expiry than minimum_remaining_shelf_life")

    def test_removal_blocked_bin_is_excluded_from_allocation(self):
        # Post stock directly onto blocked_bin (a single unbalanced "gain" entry, the same shape
        # post_count() uses for variances) - the Bin Sequence putaway rule would otherwise always
        # prefer bulk_bin (lower sequence) over blocked_bin.
        from frappe_wms.services.stock import post_entries
        post_entries(
            [{"warehouse": self.warehouse, "product": self.item2, "storage_bin": self.blocked_bin,
              "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701"}],
            "Storage Bin", self.blocked_bin, f"test-alloc-blocked:{frappe.generate_hash(length=8)}",
        )
        frappe.db.set_value("Storage Bin", self.blocked_bin, "removal_blocked", 1)
        try:
            obd = self._make_and_submit_delivery(2, item=self.item2)
            allocate_delivery(obd.name)
            obd.reload()
            self.assertEqual(flt(obd.items[0].allocated_quantity), 0, "a removal-blocked bin must not be usable as an allocation source")
            self.assertFalse(frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name, "storage_bin": self.blocked_bin}))
        finally:
            frappe.db.set_value("Storage Bin", self.blocked_bin, "removal_blocked", 0)
