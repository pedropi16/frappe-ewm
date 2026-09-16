import frappe
from frappe.tests import IntegrationTestCase


class TestErpnextStockGuard(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "GUARD-TEST-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)

        cls.unmanaged_warehouse = frappe.db.get_value("Warehouse", {"company": cls.company, "name": ["not in", [cls.wh.erpnext_warehouse]]})
        if not cls.unmanaged_warehouse:
            unmanaged = frappe.get_doc({"doctype": "Warehouse", "warehouse_name": "GUARD-TEST-UNMANAGED", "company": cls.company})
            unmanaged.insert(ignore_permissions=True)
            cls.unmanaged_warehouse = unmanaged.name

    def _material_receipt(self, warehouse, qty=1):
        return frappe.get_doc({
            "doctype": "Stock Entry", "stock_entry_type": "Material Receipt", "company": self.company,
            "items": [{"item_code": self.item, "qty": qty, "uom": self.uom, "stock_uom": self.uom,
                       "conversion_factor": 1, "t_warehouse": warehouse, "allow_zero_valuation_rate": 1}],
        })

    def test_direct_stock_entry_against_wms_managed_warehouse_is_blocked(self):
        se = self._material_receipt(self.wh.erpnext_warehouse)
        with self.assertRaises(frappe.ValidationError):
            se.insert(ignore_permissions=True)

    def test_direct_stock_entry_against_unmanaged_warehouse_is_allowed(self):
        se = self._material_receipt(self.unmanaged_warehouse)
        se.insert(ignore_permissions=True)
        se.submit()
        se.cancel()

    def test_enforcement_can_be_disabled_via_wms_settings(self):
        settings = frappe.get_single("WMS Settings")
        settings.enforce_wms_only_stock_movements = 0
        settings.save(ignore_permissions=True)
        try:
            se = self._material_receipt(self.wh.erpnext_warehouse)
            se.insert(ignore_permissions=True)
            se.submit()
            se.cancel()
        finally:
            settings.enforce_wms_only_stock_movements = 1
            settings.save(ignore_permissions=True)
