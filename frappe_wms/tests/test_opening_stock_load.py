import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.api.opening_stock import post_opening_stock_load, cancel_opening_stock_load


class TestOpeningStockLoad(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "OSL-TEST-WH"
        cls.bin1 = f"{cls.warehouse}-BIN1"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "hu_managed": 0, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.bin1):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin1, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Item", "OSL-TEST-ITEM"):
            frappe.get_doc({"doctype": "Item", "item_code": "OSL-TEST-ITEM", "item_name": "OSL Test Item", "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 1}).insert(ignore_permissions=True)
        cls.item = "OSL-TEST-ITEM"

    def test_post_and_cancel_opening_stock_load(self):
        load = frappe.get_doc({"doctype": "WMS Opening Stock Load", "warehouse": self.warehouse,
            "items": [{"item": self.item, "storage_bin": self.bin1, "stock_type": "AVAILABLE", "quantity": 25, "stock_uom": "Nos", "valuation_rate": 4}]})
        load.insert(ignore_permissions=True)

        result = post_opening_stock_load(load.name)
        self.assertEqual(result["status"], "Posted")

        balance = flt(frappe.db.get_value("WMS Stock Balance", {
            "warehouse": self.warehouse, "product": self.item, "storage_bin": self.bin1, "stock_type": "AVAILABLE", "handling_unit": ["is", "not set"],
        }, "quantity"))
        self.assertEqual(balance, 25)

        erp_qty = flt(frappe.db.get_value("Bin", {"warehouse": self.wh.erpnext_warehouse, "item_code": self.item}, "actual_qty"))
        self.assertEqual(erp_qty, 25)

        load.reload()
        self.assertTrue(load.erpnext_stock_reconciliations)
        sr = frappe.get_doc("Stock Reconciliation", load.erpnext_stock_reconciliations.split(",")[0].strip())
        self.assertEqual(sr.docstatus, 1)
        self.assertEqual(sr.purpose, "Opening Stock")
        self.assertEqual(sr.items[0].wms_stock_type, "AVAILABLE")

        cancel_opening_stock_load(load.name)
        load.reload()
        self.assertEqual(load.status, "Cancelled")
        balance_after = flt(frappe.db.get_value("WMS Stock Balance", {
            "warehouse": self.warehouse, "product": self.item, "storage_bin": self.bin1, "stock_type": "AVAILABLE", "handling_unit": ["is", "not set"],
        }, "quantity"))
        self.assertEqual(balance_after, 0)
        erp_qty_after = flt(frappe.db.get_value("Bin", {"warehouse": self.wh.erpnext_warehouse, "item_code": self.item}, "actual_qty"))
        self.assertEqual(erp_qty_after, 0)
