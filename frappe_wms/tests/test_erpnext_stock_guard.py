import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.events.erpnext_stock_guard import validate as guard_validate


class TestErpnextStockGuard(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "GUARD-TEST-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]

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

    def _fake_doc(self, doctype, **kwargs):
        # The guard only reads doc.flags/doc.doctype/doc.get(...), so a plain _dict shaped
        # like the real doctype exercises the guard's own logic without needing a fully
        # valid Sales/Purchase Invoice or Work Order (income accounts, BOMs, and so on).
        doc = frappe._dict(doctype=doctype, flags=frappe._dict(), **kwargs)
        return doc

    def test_blocks_sales_invoice_with_update_stock(self):
        doc = self._fake_doc("Sales Invoice", update_stock=1, items=[frappe._dict(warehouse=self.wh.erpnext_warehouse)])
        with self.assertRaises(frappe.ValidationError):
            guard_validate(doc)

    def test_allows_sales_invoice_without_update_stock(self):
        doc = self._fake_doc("Sales Invoice", update_stock=0, items=[frappe._dict(warehouse=self.wh.erpnext_warehouse)])
        guard_validate(doc)  # should not raise

    def test_blocks_purchase_invoice_with_update_stock(self):
        doc = self._fake_doc("Purchase Invoice", update_stock=1, items=[frappe._dict(warehouse=self.wh.erpnext_warehouse)])
        with self.assertRaises(frappe.ValidationError):
            guard_validate(doc)

    def test_blocks_subcontracting_receipt(self):
        doc = self._fake_doc("Subcontracting Receipt", items=[frappe._dict(warehouse=self.wh.erpnext_warehouse)])
        with self.assertRaises(frappe.ValidationError):
            guard_validate(doc)

    def test_blocks_work_order_doc_level_warehouse(self):
        # Defense-in-depth: Work Order doesn't post stock directly (the Stock Entries it
        # spawns are already caught by the Stock Entry entry above), but the doc-level
        # warehouse fields are guarded too in case something posts against them directly.
        doc = self._fake_doc("Work Order", fg_warehouse=self.wh.erpnext_warehouse, wip_warehouse=None, source_warehouse=None, items=[])
        with self.assertRaises(frappe.ValidationError):
            guard_validate(doc)
