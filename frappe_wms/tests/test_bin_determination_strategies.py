import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.determination import determine_destination_bin


class TestBinDeterminationStrategies(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "BINSTRAT-TEST-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        cls.bins = [f"{cls.warehouse}-BIN{i}" for i in range(3)]
        for i, bin_name in enumerate(cls.bins):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "putaway_blocked": 0, "sequence": 3 - i}).insert(ignore_permissions=True)

    def _make_rule(self, strategy):
        for existing in frappe.get_all("Bin Determination Rule", filters={"warehouse": self.warehouse, "activity": "Putaway"}, pluck="name"):
            frappe.delete_doc("Bin Determination Rule", existing, force=True, ignore_permissions=True)
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{self.warehouse}-ST", "strategy": strategy})
        rule.insert(ignore_permissions=True)
        return rule

    def test_bin_sequence_strategy_picks_lowest_sequence(self):
        self._make_rule("Bin Sequence")
        result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        # bins were created with sequence 3,2,1 for BIN0,BIN1,BIN2 -> lowest sequence is BIN2
        self.assertEqual(result, self.bins[2])

    def test_manual_selection_strategy_throws_instead_of_guessing(self):
        self._make_rule("Manual Selection")
        with self.assertRaises(frappe.ValidationError):
            determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})

    def test_first_empty_bin_strategy_avoids_occupied_bins(self):
        frappe.db.set_value("Storage Bin", self.bins[2], "current_hu_count", 1)
        self._make_rule("First Empty Bin")
        result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        self.assertNotEqual(result, self.bins[2])
        frappe.db.set_value("Storage Bin", self.bins[2], "current_hu_count", 0)

    def test_blank_destination_storage_type_falls_back_to_product_preferred_storage_type(self):
        # A rule with no destination_storage_type used to be silently dead code (the Storage
        # Bin query became "storage_type IS NULL", which can never match a reqd field) -
        # WMS Product.preferred_storage_type now fills that gap instead of leaving it unusable.
        for existing in frappe.get_all("Bin Determination Rule", filters={"warehouse": self.warehouse, "activity": "Putaway"}, pluck="name"):
            frappe.delete_doc("Bin Determination Rule", existing, force=True, ignore_permissions=True)
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "strategy": "Bin Sequence"})
        rule.insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": self.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": self.item, "stock_uom": frappe.db.get_value("Item", self.item, "stock_uom"), "warehouse_managed": 1, "active": 1, "preferred_storage_type": f"{self.warehouse}-ST"}).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("WMS Product", {"item": self.item}, "preferred_storage_type", f"{self.warehouse}-ST")
        result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        self.assertEqual(result, self.bins[2])
