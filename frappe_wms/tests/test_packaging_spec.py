import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.handling_unit import full_hu_quantity


class TestPackagingSpec(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": frappe.db.get_value("Item", cls.item, "stock_uom"),
                "warehouse_managed": 1, "active": 1, "full_hu_quantity": 100}).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("WMS Product", {"item": cls.item}, "full_hu_quantity", 100)

    def tearDown(self):
        if frappe.db.exists("Packaging Spec", self.item):
            frappe.delete_doc("Packaging Spec", self.item, force=True, ignore_permissions=True)

    def test_multi_level_lookup_computes_cumulative_quantity(self):
        frappe.get_doc({"doctype": "Packaging Spec", "item": self.item, "active": 1, "levels": [
            {"level_name": "Each", "quantity_per_level": 12},
            {"level_name": "Case", "quantity_per_level": 6},
            {"level_name": "Pallet", "quantity_per_level": 40},
        ]}).insert(ignore_permissions=True)

        self.assertEqual(full_hu_quantity(self.item, "Each"), 12)
        self.assertEqual(full_hu_quantity(self.item, "Case"), 72)
        self.assertEqual(full_hu_quantity(self.item, "Pallet"), 2880)
        self.assertEqual(full_hu_quantity(self.item), 2880, "no level_name defaults to the top (last) level")

    def test_falls_back_to_wms_product_field_when_no_spec_exists(self):
        self.assertEqual(full_hu_quantity(self.item), 100)

    def test_inactive_spec_falls_back_to_wms_product_field(self):
        frappe.get_doc({"doctype": "Packaging Spec", "item": self.item, "active": 0, "levels": [
            {"level_name": "Each", "quantity_per_level": 12},
        ]}).insert(ignore_permissions=True)
        self.assertEqual(full_hu_quantity(self.item), 100)
