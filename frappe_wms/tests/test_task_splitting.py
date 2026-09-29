import unittest

import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.task import _split_by_full_pallet, create_tasks_for_request
from frappe_wms.services.determination import determine_destination_bin


class TestSplitByFullPallet(unittest.TestCase):
    def test_no_full_quantity_configured_returns_one_chunk(self):
        self.assertEqual(_split_by_full_pallet(25, None), [25])

    def test_remaining_at_or_under_full_quantity_returns_one_chunk(self):
        self.assertEqual(_split_by_full_pallet(10, 10), [10])
        self.assertEqual(_split_by_full_pallet(7, 10), [7])

    def test_remaining_over_full_quantity_splits_into_full_pallets_plus_remainder(self):
        self.assertEqual(_split_by_full_pallet(25, 10), [10, 10, 5])

    def test_remaining_an_exact_multiple_splits_with_no_remainder_chunk(self):
        self.assertEqual(_split_by_full_pallet(20, 10), [10, 10])


class TestTaskSplitting(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "TASKSPLIT-TEST-WH"
        cls.uom = "Nos"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-RECV"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "RECV", "storage_type_name": "RECV", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.source_bin = f"{cls.warehouse}-RECV-BIN"
        if not frappe.db.exists("Storage Bin", cls.source_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.source_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-RECV", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-PALLET"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "PALLET", "storage_type_name": "PALLET", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.pallet_bins = [f"{cls.warehouse}-PALLET-BIN{i}" for i in range(3)]
        for bin_name in cls.pallet_bins:
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-PALLET", "active": 1, "maximum_hus": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
                "destination_storage_type": f"{cls.warehouse}-PALLET", "strategy": "Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TASKSPLIT-RECV-CONTAINER"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TASKSPLIT-RECV-CONTAINER", "hu_type_name": "Tasksplit Receiving Container"}).insert(ignore_permissions=True)

    def _make_item(self, item_code, full_hu_quantity=0):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1, "full_hu_quantity": full_hu_quantity}).insert(ignore_permissions=True)
        return item_code

    def _make_request(self, item_code, qty):
        # A real Putaway request always has a source_hu - the receiving flow (services/receipt.py)
        # always builds one at Goods Receipt. Splitting here models breaking down one oversized
        # received container into several storage pallets, each getting its own new destination
        # HU at confirm time - determine_destination_bin's own hu_managed check just needs SOME
        # HU in play to reason about (matching this app's own pre-existing behavior of passing
        # destination_hu=request.destination_hu or request.source_hu), even though the actual
        # created tasks' own destination_hu stays blank (a new HU per chunk).
        source_hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10),
            "hu_type": "TASKSPLIT-RECV-CONTAINER", "warehouse": self.warehouse, "current_bin": self.source_bin, "status": "Open"})
        source_hu.insert(ignore_permissions=True)
        return frappe.get_doc({
            "doctype": "Warehouse Request", "request_type": "Putaway", "warehouse": self.warehouse, "product": item_code,
            "requested_quantity": qty, "stock_uom": self.uom, "source_bin": self.source_bin, "source_hu": source_hu.name, "stock_type": "AVAILABLE",
            "reference_doctype": "Item", "reference_name": item_code, "process_type": "GR_PUTAWAY", "priority": "Normal", "status": "Open",
        }).insert(ignore_permissions=True)

    def test_request_over_full_pallet_quantity_splits_into_multiple_tasks_in_distinct_bins(self):
        item_code = self._make_item("TEST-TASKSPLIT-25", full_hu_quantity=10)
        request = self._make_request(item_code, 25)

        result = create_tasks_for_request(request.name)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)
        tasks = [frappe.get_doc("Warehouse Task", name) for name in result]
        self.assertEqual([t.planned_quantity for t in tasks], [10, 10, 5])
        destination_bins = [t.destination_bin for t in tasks]
        self.assertEqual(len(set(destination_bins)), 3, "each full-pallet chunk should land in its own bin")
        self.assertEqual(set(destination_bins), set(self.pallet_bins))
        request.reload()
        self.assertEqual(request.status, "Fully Tasked")
        self.assertEqual(request.created_quantity, 25)

    def test_request_at_or_under_full_pallet_quantity_creates_a_single_task(self):
        item_code = self._make_item("TEST-TASKSPLIT-8", full_hu_quantity=10)
        request = self._make_request(item_code, 8)

        result = create_tasks_for_request(request.name)

        self.assertIsInstance(result, str)
        task = frappe.get_doc("Warehouse Task", result)
        self.assertEqual(task.planned_quantity, 8)
        self.assertEqual(task.idempotency_key, f"WT:{request.name}")

    def test_no_full_hu_quantity_configured_creates_a_single_task_regardless_of_size(self):
        item_code = self._make_item("TEST-TASKSPLIT-NOSPEC")
        request = self._make_request(item_code, 40)

        result = create_tasks_for_request(request.name)

        self.assertIsInstance(result, str)
        self.assertEqual(frappe.db.get_value("Warehouse Task", result, "planned_quantity"), 40)

    def test_fixed_destination_bin_disables_splitting_even_over_full_pallet_quantity(self):
        item_code = self._make_item("TEST-TASKSPLIT-FIXEDBIN", full_hu_quantity=10)
        request = frappe.get_doc({
            "doctype": "Warehouse Request", "request_type": "Putaway", "warehouse": self.warehouse, "product": item_code,
            "requested_quantity": 25, "stock_uom": self.uom, "source_bin": self.source_bin, "destination_bin": self.pallet_bins[0],
            "stock_type": "AVAILABLE", "reference_doctype": "Item", "reference_name": item_code,
            "process_type": "GR_PUTAWAY", "priority": "Normal", "status": "Open",
        }).insert(ignore_permissions=True)

        result = create_tasks_for_request(request.name)

        self.assertIsInstance(result, str)
        self.assertEqual(frappe.db.get_value("Warehouse Task", result, "planned_quantity"), 25)


class TestReservedHuCountsInBinDetermination(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "RESERVEDHU-TEST-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.bins = [f"{cls.warehouse}-BIN{i}" for i in range(2)]
        for i, bin_name in enumerate(cls.bins):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 2 - i}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
                "destination_storage_type": f"{cls.warehouse}-ST", "strategy": "First Empty Bin"}).insert(ignore_permissions=True)

    def test_reserved_hu_counts_are_treated_as_already_occupied(self):
        # bins were created with sequence 2,1 for BIN0,BIN1 -> lowest sequence (BIN1) wins ties.
        without_reservation = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item,
            "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        self.assertEqual(without_reservation, self.bins[1])

        reserved = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item,
            "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU",
            "reserved_hu_counts": {self.bins[1]: 1}})
        self.assertEqual(reserved, self.bins[0], "a bin reserved earlier in the same call must not be picked again")
