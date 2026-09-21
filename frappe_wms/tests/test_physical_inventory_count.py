import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.services.task import create_and_confirm_move
from frappe_wms.services.inventory_count import snapshot_count, record_counts, post_count


class TestPhysicalInventoryCount(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PIC-TEST-WH"
        cls.bin_a = f"{cls.warehouse}-A"
        cls.bin_b = f"{cls.warehouse}-B"
        cls.uom = "Nos"
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name in (cls.bin_a, cls.bin_b):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "PIC-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PIC-PALLET", "hu_type_name": "Pic Pallet"}).insert(ignore_permissions=True)

    def _make_item(self, item_code):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        return item_code

    def _erpnext_qty(self, item):
        return flt(frappe.db.get_value("Bin", {"warehouse": self.wh.erpnext_warehouse, "item_code": item}, "actual_qty"))

    def _wms_qty(self, item):
        return flt(frappe.db.sql("select sum(quantity) from `tabWMS Stock Balance` where warehouse=%s and product=%s", (self.warehouse, item))[0][0])

    def _receive(self, item, bin_name, qty):
        # Goes through the real Goods Receipt -> ERPNext mirror path (not a raw ledger seed)
        # so ERPNext's Bin/Stock Ledger already has prior stock for this item/warehouse -
        # otherwise ERPNext treats the count's first correction as an opening entry.
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PIC-PALLET", "warehouse": self.warehouse, "current_bin": bin_name, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": bin_name,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": bin_name,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return hu

    def _make_count(self, item, storage_bin=None):
        return frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse,
            "storage_bin": storage_bin, "product": item, "status": "Draft"}).insert(ignore_permissions=True)

    def test_negative_variance_posts_a_material_issue_and_closes_drift(self):
        item = self._make_item("TEST-PIC-ITEM-1")
        self._receive(item, self.bin_a, 10)
        count = self._make_count(item, storage_bin=self.bin_a)
        snapshot_count(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: 8})
        qty_before = self._erpnext_qty(item)
        result = post_count(count.name)
        self.assertEqual(result["status"], "Posted")
        count.reload()
        self.assertFalse(count.erpnext_gain_stock_entry)
        self.assertTrue(count.erpnext_loss_stock_entry)
        se = frappe.get_doc("Stock Entry", count.erpnext_loss_stock_entry)
        self.assertEqual(se.stock_entry_type, "Material Issue")
        self.assertEqual(se.docstatus, 1)
        self.assertEqual(se.items[0].qty, 2)
        self.assertEqual(se.items[0].wms_stock_type, "AVAILABLE")
        self.assertEqual(self._erpnext_qty(item), qty_before - 2)
        self.assertEqual(self._wms_qty(item), self._erpnext_qty(item), "drift check must stay empty after posting")

    def test_positive_variance_posts_a_material_receipt(self):
        item = self._make_item("TEST-PIC-ITEM-1B")
        self._receive(item, self.bin_a, 10)
        count = self._make_count(item, storage_bin=self.bin_a)
        snapshot_count(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: 13})
        qty_before = self._erpnext_qty(item)
        post_count(count.name)
        count.reload()
        self.assertFalse(count.erpnext_loss_stock_entry)
        self.assertTrue(count.erpnext_gain_stock_entry)
        se = frappe.get_doc("Stock Entry", count.erpnext_gain_stock_entry)
        self.assertEqual(se.stock_entry_type, "Material Receipt")
        self.assertEqual(se.items[0].qty, 3)
        self.assertEqual(se.items[0].to_wms_stock_type, "AVAILABLE")
        self.assertEqual(self._erpnext_qty(item), qty_before + 3)

    def test_multi_bin_same_product_variances_are_grouped_into_one_row(self):
        item = self._make_item("TEST-PIC-ITEM-2")
        hu = self._receive(item, self.bin_a, 10)
        create_and_confirm_move(warehouse=self.warehouse, product=item, quantity=5, stock_uom=self.uom,
            stock_type="AVAILABLE", source_bin=self.bin_a, source_hu=hu.name, destination_bin=self.bin_b, destination_hu=hu.name)
        count = self._make_count(item)
        snapshot_count(count.name)
        count.reload()
        counted = {}
        for row in count.items:
            counted[row.name] = 4 if row.storage_bin == self.bin_a else 8
        record_counts(count.name, counted)
        qty_before = self._erpnext_qty(item)
        post_count(count.name)
        count.reload()
        se = frappe.get_doc("Stock Entry", count.erpnext_gain_stock_entry)
        matching = [i for i in se.items if i.item_code == item]
        self.assertEqual(len(matching), 1, "same product across two bins must collapse into one Stock Entry row")
        self.assertEqual(flt(matching[0].qty), 2)
        self.assertEqual(self._erpnext_qty(item), qty_before + 2)

    def test_net_zero_variance_across_bins_produces_no_entries(self):
        item = self._make_item("TEST-PIC-ITEM-3")
        hu = self._receive(item, self.bin_a, 12)
        create_and_confirm_move(warehouse=self.warehouse, product=item, quantity=6, stock_uom=self.uom,
            stock_type="AVAILABLE", source_bin=self.bin_a, source_hu=hu.name, destination_bin=self.bin_b, destination_hu=hu.name)
        count = self._make_count(item)
        snapshot_count(count.name)
        count.reload()
        counted = {}
        for row in count.items:
            counted[row.name] = 4 if row.storage_bin == self.bin_a else 8
        record_counts(count.name, counted)
        post_count(count.name)
        count.reload()
        self.assertFalse(count.erpnext_gain_stock_entry, "net-zero variance across bins must not create a gain entry")
        self.assertFalse(count.erpnext_loss_stock_entry, "net-zero variance across bins must not create a loss entry")

    def test_zero_variance_count_produces_no_entries(self):
        item = self._make_item("TEST-PIC-ITEM-4")
        self._receive(item, self.bin_a, 9)
        count = self._make_count(item, storage_bin=self.bin_a)
        snapshot_count(count.name)
        count.reload()
        record_counts(count.name, {count.items[0].name: 9})
        post_count(count.name)
        count.reload()
        self.assertFalse(count.erpnext_gain_stock_entry)
        self.assertFalse(count.erpnext_loss_stock_entry)

    def test_mixed_gain_and_loss_items_post_both_entries(self):
        gain_item = self._make_item("TEST-PIC-ITEM-5A")
        loss_item = self._make_item("TEST-PIC-ITEM-5B")
        self._receive(gain_item, self.bin_a, 5)
        self._receive(loss_item, self.bin_a, 5)
        count = frappe.get_doc({"doctype": "WMS Physical Inventory Count", "warehouse": self.warehouse,
            "storage_bin": self.bin_a, "status": "Draft"}).insert(ignore_permissions=True)
        snapshot_count(count.name)
        count.reload()
        counted = {}
        for row in count.items:
            counted[row.name] = 7 if row.product == gain_item else 3
        record_counts(count.name, counted)
        post_count(count.name)
        count.reload()
        self.assertTrue(count.erpnext_gain_stock_entry)
        self.assertTrue(count.erpnext_loss_stock_entry)
        self.assertNotEqual(count.erpnext_gain_stock_entry, count.erpnext_loss_stock_entry)
