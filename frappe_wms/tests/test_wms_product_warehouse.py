import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.determination import determine_destination_bin


class TestWmsProductWarehouse(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PRODWH-TEST-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code in ("GLOBAL", "OVERRIDE"):
            if not frappe.db.exists("Storage Type", f"{cls.warehouse}-{code}"):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code, "storage_type_name": code, "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for code in ("GLOBAL", "OVERRIDE"):
            bin_name = f"{cls.warehouse}-{code}-BIN"
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-{code}", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1, "preferred_storage_type": f"{cls.warehouse}-GLOBAL"}).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("WMS Product", {"item": cls.item}, "preferred_storage_type", f"{cls.warehouse}-GLOBAL")
        for existing in frappe.get_all("Bin Determination Rule", filters={"warehouse": cls.warehouse, "activity": "Putaway"}, pluck="name"):
            frappe.delete_doc("Bin Determination Rule", existing, force=True, ignore_permissions=True)
        frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "strategy": "Bin Sequence"}).insert(ignore_permissions=True)

    def _context(self):
        return {"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"}

    def test_falls_back_to_global_preferred_storage_type_when_no_override_exists(self):
        if frappe.db.exists("WMS Product Warehouse", f"{self.warehouse}-{self.item}"):
            frappe.delete_doc("WMS Product Warehouse", f"{self.warehouse}-{self.item}", force=True, ignore_permissions=True)
        result = determine_destination_bin(self._context())
        self.assertEqual(result, f"{self.warehouse}-GLOBAL-BIN")

    def test_per_warehouse_override_wins_over_global_preferred_storage_type(self):
        if not frappe.db.exists("WMS Product Warehouse", f"{self.warehouse}-{self.item}"):
            frappe.get_doc({"doctype": "WMS Product Warehouse", "item": self.item, "warehouse": self.warehouse, "preferred_storage_type": f"{self.warehouse}-OVERRIDE", "active": 1}).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("WMS Product Warehouse", f"{self.warehouse}-{self.item}", "preferred_storage_type", f"{self.warehouse}-OVERRIDE")
        result = determine_destination_bin(self._context())
        self.assertEqual(result, f"{self.warehouse}-OVERRIDE-BIN")
