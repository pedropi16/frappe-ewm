import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.services.inventory_count import snapshot_count, record_counts, post_count, request_recount, approve_variance


class TestCountTolerance(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "CTG-TEST-WH"
        cls.bin_a = f"{cls.warehouse}-A"
        cls.uom = "Nos"
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.bin_a):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin_a, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "CTG-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "CTG-PALLET", "hu_type_name": "CTG Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Count Tolerance Group", {"warehouse": cls.warehouse}):
            frappe.get_doc({"doctype": "Count Tolerance Group", "priority": 1, "warehouse": cls.warehouse, "item_group": cls.item_group,
                "tolerance_percentage": 5, "tolerance_quantity": 1, "requires_recount": 1, "requires_approval": 1, "active": 1}).insert(ignore_permissions=True)

    def _make_item(self, item_code):
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": self.item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        return item_code

    def _receive(self, item, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "CTG-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.bin_a,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.bin_a,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return hu

    def _make_count(self, item):
        return frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse,
            "storage_bin": self.bin_a, "product": item, "status": "Draft"}).insert(ignore_permissions=True)

    def _count_and_record(self, item, qty_received, qty_counted):
        self._receive(item, qty_received)
        count = self._make_count(item)
        snapshot_count(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: qty_counted})
        return count

    def test_variance_within_tolerance_posts_immediately(self):
        item = self._make_item("TEST-CTG-ITEM-1")
        count = self._count_and_record(item, 100, 101)  # 1% variance, within both quantity(1) and percentage(5) tolerance
        result = post_count(count.name)
        self.assertEqual(result["status"], "Posted")
        count.reload()
        self.assertEqual(count.items[0].status, "Posted")

    def test_variance_outside_tolerance_holds_for_recount(self):
        item = self._make_item("TEST-CTG-ITEM-2")
        count = self._count_and_record(item, 100, 80)  # 20% variance, outside tolerance
        result = post_count(count.name)
        self.assertEqual(result["status"], "Under Review")
        count.reload()
        self.assertEqual(count.items[0].status, "Pending Recount")
        self.assertEqual(count.items[0].tolerance_group, frappe.get_all("Count Tolerance Group", filters={"warehouse": self.warehouse}, pluck="name")[0])
        self.assertFalse(count.erpnext_gain_stock_entry)
        self.assertFalse(count.erpnext_loss_stock_entry)

    def test_no_matching_tolerance_group_posts_unconditionally(self):
        other_item_group = frappe.get_all("Item Group", filters={"name": ["!=", self.item_group]}, limit=1, pluck="name")
        if not other_item_group:
            other_item_group = [frappe.get_doc({"doctype": "Item Group", "item_group_name": "CTG-Test-Other-Group", "parent_item_group": "All Item Groups", "is_group": 0}).insert(ignore_permissions=True).name]
        item_code = "TEST-CTG-ITEM-NOMATCH"
        frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": other_item_group[0], "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        count = self._count_and_record(item_code, 100, 50)  # huge variance, but no matching tolerance group
        result = post_count(count.name)
        self.assertEqual(result["status"], "Posted")

    def test_recount_can_be_re_recorded_and_re_evaluated(self):
        item = self._make_item("TEST-CTG-ITEM-3")
        count = self._count_and_record(item, 100, 80)
        post_count(count.name)
        count.reload()
        self.assertEqual(count.items[0].status, "Pending Recount")

        request_recount(count.name)
        count.reload()
        self.assertEqual(count.items[0].status, "Open")
        self.assertEqual(count.items[0].recount_count, 1)
        self.assertEqual(count.status, "Counting")

        record_counts(count.name, {count.items[0].name: 99})  # this time within tolerance
        count.reload()
        self.assertEqual(count.status, "Counted")
        result = post_count(count.name)
        self.assertEqual(result["status"], "Posted")

    def test_recount_still_out_of_tolerance_escalates_to_pending_approval(self):
        item = self._make_item("TEST-CTG-ITEM-4")
        count = self._count_and_record(item, 100, 80)
        post_count(count.name)
        request_recount(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: 81})  # recounted, still way out of tolerance
        result = post_count(count.name)
        self.assertEqual(result["status"], "Under Review")
        count.reload()
        self.assertEqual(count.items[0].status, "Pending Approval", "a second out-of-tolerance recount escalates instead of looping back to Pending Recount")

    def test_approve_variance_posts_and_closes_count(self):
        item = self._make_item("TEST-CTG-ITEM-5")
        count = self._count_and_record(item, 100, 80)
        post_count(count.name)
        request_recount(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: 81})
        post_count(count.name)

        result = approve_variance(count.name, remarks="confirmed shrinkage")
        self.assertEqual(result["status"], "Posted")
        count.reload()
        self.assertEqual(count.items[0].status, "Posted")
        self.assertTrue(count.approved_by)
        self.assertTrue(count.approved_at)
        self.assertEqual(count.review_remarks, "confirmed shrinkage")
        self.assertTrue(count.erpnext_loss_stock_entry)

    def test_approve_variance_rejected_when_not_under_review(self):
        item = self._make_item("TEST-CTG-ITEM-6")
        count = self._count_and_record(item, 100, 101)
        post_count(count.name)  # posts immediately, doc is now "Posted" not "Under Review"
        with self.assertRaises(frappe.ValidationError):
            approve_variance(count.name)

    def test_approve_variance_requires_supervisor_role(self):
        item = self._make_item("TEST-CTG-ITEM-7")
        count = self._count_and_record(item, 100, 80)
        post_count(count.name)

        tester_email = "ctg-no-role-tester@example.com"
        if not frappe.db.exists("User", tester_email):
            frappe.get_doc({"doctype": "User", "email": tester_email, "first_name": "CTG No Role Tester", "send_welcome_email": 0}).insert(ignore_permissions=True)
        frappe.set_user(tester_email)
        try:
            with self.assertRaises(frappe.PermissionError):
                approve_variance(count.name)
        finally:
            frappe.set_user("Administrator")
