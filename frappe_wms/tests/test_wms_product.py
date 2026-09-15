import frappe
from frappe.tests import IntegrationTestCase


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
