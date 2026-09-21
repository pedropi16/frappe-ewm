import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate


class TestWmsProduct(IntegrationTestCase):
    def test_non_stock_item_cannot_be_warehouse_managed(self):
        item = frappe.get_all("Item", filters={"is_stock_item": 0}, limit=1, pluck="name")
        if not item:
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            doc = frappe.get_doc({"doctype": "Item", "item_code": "WMS-TEST-NONSTOCK", "item_group": item_group, "is_stock_item": 0, "stock_uom": "Nos"})
            doc.insert(ignore_permissions=True)
            item = [doc.name]
        item = item[0]
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": "Nos", "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)

    def test_stock_uom_is_defaulted_from_item(self):
        item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        item_stock_uom = frappe.db.get_value("Item", item, "stock_uom")
        if frappe.db.exists("WMS Product", {"item": item}):
            self.skipTest("item already has a WMS Product")
        doc = frappe.get_doc({"doctype": "WMS Product", "item": item, "warehouse_managed": 1, "active": 1})
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.stock_uom, item_stock_uom)


class TestWmsProductReceiptControls(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMSPRODUCT-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.uom = "Nos"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.recv_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.recv_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-GR", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "WMSPRODUCT-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WMSPRODUCT-PALLET", "hu_type_name": "WmsProduct Pallet"}).insert(ignore_permissions=True)

    def _make_item(self, item_code, **product_fields):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1, **product_fields}).insert(ignore_permissions=True)
        return item_code

    def _make_gr(self, item_code, qty=1, serial_no=None, batch_no=None):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "WMSPRODUCT-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item_code, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item_code, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE", "serial_no": serial_no, "batch_no": batch_no}]})
        gr.insert(ignore_permissions=True)
        return gr

    def test_serial_control_required_at_receipt_is_enforced(self):
        item_code = self._make_item("TEST-WMSPRODUCT-SERIAL", serial_control="Required at Receipt")
        gr = self._make_gr(item_code)
        with self.assertRaises(frappe.ValidationError):
            gr.submit()

    def test_batch_control_is_enforced(self):
        item_code = self._make_item("TEST-WMSPRODUCT-BATCH", batch_control=1)
        gr = self._make_gr(item_code)
        with self.assertRaises(frappe.ValidationError):
            gr.submit()

    def test_shelf_life_expiry_is_stamped_on_receipt(self):
        item_code = self._make_item("TEST-WMSPRODUCT-SLED", shelf_life_days=30)
        gr = self._make_gr(item_code, qty=4)
        gr.submit()
        expiry = frappe.db.get_value("WMS Stock Balance", {"product": item_code, "quantity": [">", 0]}, "shelf_life_expiry_date")
        self.assertEqual(getdate(expiry), getdate(add_days(getdate(gr.posting_datetime), 30)))

    def test_no_shelf_life_days_leaves_expiry_blank(self):
        item_code = self._make_item("TEST-WMSPRODUCT-NOSLED")
        gr = self._make_gr(item_code, qty=2)
        gr.submit()
        expiry = frappe.db.get_value("WMS Stock Balance", {"product": item_code, "quantity": [">", 0]}, "shelf_life_expiry_date")
        self.assertFalse(expiry)
