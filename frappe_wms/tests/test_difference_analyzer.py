import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.inventory_count import snapshot_count, record_counts, post_count, request_recount, analyze_differences


class TestDifferenceAnalyzer(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "DIFF-TEST-WH"
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
        if not frappe.db.exists("Handling Unit Type", "DIFF-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "DIFF-PALLET", "hu_type_name": "Diff Pallet"}).insert(ignore_permissions=True)
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
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "DIFF-PALLET", "warehouse": self.warehouse, "current_bin": self.bin_a, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.bin_a,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.bin_a,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return hu

    def _count_record_post(self, item, qty_received, qty_counted):
        self._receive(item, qty_received)
        count = frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse,
            "storage_bin": self.bin_a, "product": item, "status": "Draft"}).insert(ignore_permissions=True)
        snapshot_count(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: qty_counted})
        post_count(count.name)
        count.reload()
        return count

    def test_gain_and_loss_aggregate_per_product(self):
        gain_item = self._make_item("TEST-DIFF-GAIN")
        loss_item = self._make_item("TEST-DIFF-LOSS")
        self._count_record_post(gain_item, 100, 103)  # within tolerance, posts immediately
        self._count_record_post(loss_item, 100, 97)  # within tolerance, posts immediately

        rows = {r.product: r for r in analyze_differences(self.warehouse)}
        self.assertEqual(rows[gain_item].total_gain, 3)
        self.assertEqual(rows[gain_item].total_loss, 0)
        self.assertEqual(rows[gain_item].net_variance, 3)
        self.assertEqual(rows[loss_item].total_loss, -3)
        self.assertEqual(rows[loss_item].net_variance, -3)

    def test_over_tolerance_event_is_flagged_after_approval(self):
        item = self._make_item("TEST-DIFF-OVER")
        count = self._count_record_post(item, 100, 80)  # out of tolerance -> Pending Recount
        self.assertEqual(count.status, "Under Review")
        request_recount(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: 81})  # still out of tolerance -> Pending Approval
        post_count(count.name)
        from frappe_wms.services.inventory_count import approve_variance
        approve_variance(count.name)

        rows = {r.product: r for r in analyze_differences(self.warehouse)}
        self.assertEqual(rows[item].over_tolerance_events, 1)

    def test_within_tolerance_row_is_not_flagged(self):
        item = self._make_item("TEST-DIFF-NORMAL")
        self._count_record_post(item, 100, 101)
        rows = {r.product: r for r in analyze_differences(self.warehouse)}
        self.assertEqual(rows[item].over_tolerance_events, 0)

    def test_filters_by_product(self):
        item_a = self._make_item("TEST-DIFF-FILTER-A")
        item_b = self._make_item("TEST-DIFF-FILTER-B")
        self._count_record_post(item_a, 50, 52)
        self._count_record_post(item_b, 50, 48)
        rows = analyze_differences(self.warehouse, product=item_a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].product, item_a)
