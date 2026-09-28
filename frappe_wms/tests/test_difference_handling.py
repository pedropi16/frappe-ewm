import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.services.stock import post_entries
from frappe_wms.services.task import confirm_task, raise_exception
from frappe_wms.services.difference import clear_over_difference, clear_short_difference, list_open_differences

# Phase 4: "Difference handling for short and over confirmations, cleared through a difference
# bin." An Over difference (confirm_task) is real stock a resource found beyond a task's
# planned_quantity - previously a flat ValidationError, with no way to record what was actually
# found. A Short difference (raise_exception's revised_quantity) is logged whenever a task closes
# below its ORIGINAL planned_quantity - previously the shortfall just vanished into a lowered
# planned_quantity with no trace at all.


class TestDifferenceHandling(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "DIFF-TEST-WH"
        cls.source_bin = f"{cls.warehouse}-SOURCE"
        cls.dest_bin = f"{cls.warehouse}-DEST"
        cls.dest_bin2 = f"{cls.warehouse}-DEST2"
        cls.diff_bin = f"{cls.warehouse}-DIFFBIN"
        cls.uom = "Nos"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "allow_mixed_products": 1, "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.source_bin, cls.dest_bin, cls.dest_bin2, cls.diff_bin):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not wh.default_difference_bin:
            wh.default_difference_bin = cls.diff_bin
            wh.save(ignore_permissions=True)
        if not frappe.db.exists("WMS Exception Code", "TEST-DIFF-SHORT"):
            frappe.get_doc({"doctype": "WMS Exception Code", "exception_code": "TEST-DIFF-SHORT", "exception_name": "Test Difference Short",
                "category": "Stock", "allows_quantity_change": 1, "active": 1}).insert(ignore_permissions=True)

    def _make_item(self, item_code):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        return item_code

    def _seed_source(self, item_code, qty, bin_name=None):
        post_entries([{"warehouse": self.warehouse, "product": item_code, "storage_bin": bin_name or self.source_bin,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701"}],
            "Storage Bin", bin_name or self.source_bin, f"DIFF-SEED-{frappe.generate_hash(length=10)}")

    def _make_task(self, item_code, planned_quantity, destination_bin=None):
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse, "product": item_code,
            "planned_quantity": planned_quantity, "stock_uom": self.uom, "source_bin": self.source_bin,
            "destination_bin": destination_bin or self.dest_bin, "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        task.insert(ignore_permissions=True)
        return task

    def _balance(self, item_code, bin_name):
        return flt(frappe.db.get_value("WMS Stock Balance", {"product": item_code, "storage_bin": bin_name, "stock_type": "AVAILABLE"}, "quantity"))

    def test_over_confirmation_caps_the_task_and_records_a_difference(self):
        item_code = self._make_item("TEST-DIFF-OVER")
        self._seed_source(item_code, 20)
        task = self._make_task(item_code, 10)

        result = confirm_task(task.name, confirmed_quantity=15)

        self.assertEqual(result["quantity"], 10)
        self.assertIn("difference", result)
        task.reload()
        self.assertEqual(task.confirmed_quantity, 10)
        self.assertEqual(task.status, "Confirmed")
        self.assertEqual(self._balance(item_code, self.dest_bin), 10)
        self.assertEqual(self._balance(item_code, self.diff_bin), 5)

        diff = frappe.get_doc("WMS Task Difference", result["difference"])
        self.assertEqual(diff.direction, "Over")
        self.assertEqual(diff.difference_quantity, 5)
        self.assertEqual(diff.storage_bin, self.diff_bin)
        self.assertEqual(diff.status, "Open")
        self.assertEqual(diff.warehouse_task, task.name)

    def test_confirmation_at_or_under_planned_quantity_creates_no_difference(self):
        item_code = self._make_item("TEST-DIFF-NORMAL")
        self._seed_source(item_code, 20)
        task = self._make_task(item_code, 10)
        before = frappe.db.count("WMS Task Difference")

        result = confirm_task(task.name, confirmed_quantity=10)

        self.assertNotIn("difference", result)
        self.assertEqual(frappe.db.count("WMS Task Difference"), before)

    def test_over_confirmation_without_a_difference_bin_configured_is_rejected_atomically(self):
        nodiff_warehouse = "DIFF-TEST-NOBIN-WH"
        source_bin = f"{nodiff_warehouse}-SOURCE"
        dest_bin = f"{nodiff_warehouse}-DEST"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", nodiff_warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": nodiff_warehouse, "warehouse_name": nodiff_warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{nodiff_warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": nodiff_warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (source_bin, dest_bin):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": nodiff_warehouse, "storage_type": f"{nodiff_warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        item_code = self._make_item("TEST-DIFF-NOBIN")
        post_entries([{"warehouse": nodiff_warehouse, "product": item_code, "storage_bin": source_bin,
            "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 20, "movement_type": "701"}],
            "Storage Bin", source_bin, f"DIFF-NOBIN-SEED-{frappe.generate_hash(length=10)}")
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": nodiff_warehouse, "product": item_code,
            "planned_quantity": 10, "stock_uom": self.uom, "source_bin": source_bin, "destination_bin": dest_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE", "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        task.insert(ignore_permissions=True)

        with self.assertRaises(frappe.ValidationError):
            confirm_task(task.name, confirmed_quantity=15)

        task.reload()
        self.assertEqual(task.confirmed_quantity, 0)
        self.assertEqual(task.status, "Open")
        self.assertFalse(frappe.db.exists("WMS Stock Ledger Entry", {"reference_doctype": "Warehouse Task", "reference_name": task.name}))

    def test_clear_over_difference_moves_stock_out_of_the_difference_bin(self):
        item_code = self._make_item("TEST-DIFF-CLEAR")
        self._seed_source(item_code, 20)
        task = self._make_task(item_code, 10)
        result = confirm_task(task.name, confirmed_quantity=14)
        difference_name = result["difference"]

        clear_result = clear_over_difference(difference_name, destination_bin=self.dest_bin2)

        self.assertEqual(clear_result["status"], "Cleared")
        self.assertEqual(self._balance(item_code, self.diff_bin), 0)
        self.assertEqual(self._balance(item_code, self.dest_bin2), 4)
        diff = frappe.get_doc("WMS Task Difference", difference_name)
        self.assertEqual(diff.status, "Cleared")
        self.assertEqual(diff.cleared_to_bin, self.dest_bin2)
        self.assertTrue(diff.cleared_by)
        self.assertTrue(diff.cleared_at)

        with self.assertRaises(frappe.ValidationError):
            clear_over_difference(difference_name, destination_bin=self.dest_bin2)

    def test_short_confirmation_via_raise_exception_records_a_difference(self):
        item_code = self._make_item("TEST-DIFF-SHORT-ITEM")
        self._seed_source(item_code, 20)
        task = self._make_task(item_code, 10)
        confirm_task(task.name, confirmed_quantity=4)

        result = raise_exception(task.name, "TEST-DIFF-SHORT", remarks="only 4 found", revised_quantity=4)

        self.assertEqual(result["status"], "Confirmed")
        self.assertIn("difference", result)
        diff = frappe.get_doc("WMS Task Difference", result["difference"])
        self.assertEqual(diff.direction, "Short")
        self.assertEqual(diff.difference_quantity, 6)
        self.assertEqual(diff.planned_quantity, 10, "must record the ORIGINAL planned_quantity, not the revised one")
        self.assertEqual(diff.exception_code, "TEST-DIFF-SHORT")
        self.assertEqual(diff.status, "Open")

        clear_result = clear_short_difference(diff.name, remarks="acknowledged")
        self.assertEqual(clear_result["status"], "Cleared")
        diff.reload()
        self.assertEqual(diff.status, "Cleared")
        self.assertEqual(diff.clearance_remarks, "acknowledged")

    def test_list_open_differences_returns_only_open_ones(self):
        item_code = self._make_item("TEST-DIFF-LIST")
        self._seed_source(item_code, 20)
        task = self._make_task(item_code, 10)
        result = confirm_task(task.name, confirmed_quantity=13)
        open_names = {d.name for d in list_open_differences(warehouse=self.warehouse)}
        self.assertIn(result["difference"], open_names)
        clear_over_difference(result["difference"], destination_bin=self.dest_bin2)
        open_names_after = {d.name for d in list_open_differences(warehouse=self.warehouse)}
        self.assertNotIn(result["difference"], open_names_after)
