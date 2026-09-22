import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.vas import list_open_vas_orders, get_vas_order, complete_activity, create_vas_order_from_packaging_spec
from frappe_wms.services.stock import post_entries


class TestVAS(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-VAS-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-VAS-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-VAS-PALLET", "hu_type_name": "Test VAS Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.bin_a = f"{cls.warehouse}-A"
        if not frappe.db.exists("Storage Bin", cls.bin_a):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin_a, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-A", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

    def _make_vas_order(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-VAS-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"})
        hu.insert(ignore_permissions=True)
        order = frappe.get_doc({
            "doctype": "VAS Order", "handling_unit": hu.name, "warehouse": self.warehouse, "work_center_bin": self.bin_a,
            "activities": [
                {"step_no": 1, "activity_type": "Label", "instruction": "Apply compliance label"},
                {"step_no": 2, "activity_type": "Kit", "instruction": "Insert accessory kit"},
            ],
        })
        order.insert(ignore_permissions=True)
        return order

    def test_completing_all_activities_completes_the_order(self):
        order = self._make_vas_order()
        first_row, second_row = order.activities

        result = complete_activity(order.name, first_row.name)
        self.assertEqual(result["status"], "In Process")

        result = complete_activity(order.name, second_row.name)
        self.assertEqual(result["status"], "Completed")

        order.reload()
        self.assertEqual(order.status, "Completed")
        self.assertTrue(order.completed_at)
        self.assertTrue(all(r.completed for r in order.activities))

    def test_list_open_vas_orders_is_warehouse_scoped_and_reports_progress(self):
        order = self._make_vas_order()
        orders = list_open_vas_orders()
        match = next(o for o in orders if o.name == order.name)
        self.assertEqual(match.activity_count, 2)
        self.assertEqual(match.completed_count, 0)

        detail = get_vas_order(order.name)
        self.assertEqual(len(detail["activities"]), 2)

    def test_completing_an_activity_records_duration(self):
        order = self._make_vas_order()
        row = order.activities[0]
        self.assertTrue(row.started_at)
        complete_activity(order.name, row.name)
        row.reload()
        self.assertIsNotNone(row.duration_seconds)
        self.assertGreaterEqual(row.duration_seconds, 0)

    def test_create_vas_order_from_packaging_spec_builds_one_activity_per_level(self):
        item_code = "TEST-VAS-PACKSPEC-ITEM"
        item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": "Nos", "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": "Nos", "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Packaging Spec", item_code):
            frappe.get_doc({"doctype": "Packaging Spec", "item": item_code, "active": 1, "levels": [
                {"level_name": "Each", "quantity_per_level": 12},
                {"level_name": "Case", "quantity_per_level": 6},
            ]}).insert(ignore_permissions=True)

        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-VAS-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"})
        hu.insert(ignore_permissions=True)
        post_entries([{
            "warehouse": self.warehouse, "product": item_code, "storage_bin": self.bin_a, "handling_unit": hu.name,
            "stock_type": "AVAILABLE", "stock_uom": "Nos", "quantity": 72, "movement_type": "701",
        }], "Storage Bin", self.bin_a, f"test-vas-packspec-seed:{frappe.generate_hash(length=8)}")

        order_name = create_vas_order_from_packaging_spec(hu.name, self.bin_a)
        order = frappe.get_doc("VAS Order", order_name)
        self.assertEqual(len(order.activities), 2)
        self.assertEqual(order.activities[0].packaging_spec_level, "Each")
        self.assertEqual(order.activities[0].quantity, 12)
        self.assertEqual(order.activities[1].packaging_spec_level, "Case")
        self.assertEqual(order.activities[1].quantity, 72)

    def test_create_vas_order_from_packaging_spec_without_a_spec_throws(self):
        item_code = "TEST-VAS-NOSPEC-ITEM"
        item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": "Nos", "is_stock_item": 1}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-VAS-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"})
        hu.insert(ignore_permissions=True)
        post_entries([{
            "warehouse": self.warehouse, "product": item_code, "storage_bin": self.bin_a, "handling_unit": hu.name,
            "stock_type": "AVAILABLE", "stock_uom": "Nos", "quantity": 5, "movement_type": "701",
        }], "Storage Bin", self.bin_a, f"test-vas-nospec-seed:{frappe.generate_hash(length=8)}")
        with self.assertRaises(frappe.ValidationError):
            create_vas_order_from_packaging_spec(hu.name, self.bin_a)
