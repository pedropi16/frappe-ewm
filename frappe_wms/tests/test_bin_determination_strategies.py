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

    def test_general_storage_strategy_behaves_like_bin_sequence(self):
        self._make_rule("General Storage")
        result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        self.assertEqual(result, self.bins[2])

    def test_pallet_strategy_behaves_like_first_empty_bin(self):
        frappe.db.set_value("Storage Bin", self.bins[2], "current_hu_count", 1)
        self._make_rule("Pallet")
        result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        self.assertNotEqual(result, self.bins[2])
        frappe.db.set_value("Storage Bin", self.bins[2], "current_hu_count", 0)

    def test_bulk_strategy_prefers_occupied_bin_with_most_remaining_capacity(self):
        # maximum_hus is a NOT NULL column - 0 is this codebase's existing "no limit" sentinel
        # (bin_violations()/_apply_bin_strategy both treat falsy maximum_hus as unlimited), so
        # cleanup must reset to 0, never None.
        try:
            for bin_name, maximum_hus, current_hu_count in ((self.bins[0], 10, 8), (self.bins[1], 10, 2), (self.bins[2], 0, 0)):
                frappe.db.set_value("Storage Bin", bin_name, {"maximum_hus": maximum_hus, "current_hu_count": current_hu_count})
            from frappe_wms.services.stock import post_entries
            post_entries(
                [{"warehouse": self.warehouse, "product": self.item, "storage_bin": self.bins[1],
                  "stock_type": "AVAILABLE", "stock_uom": frappe.db.get_value("Item", self.item, "stock_uom"), "quantity": 1, "movement_type": "701"}],
                "Storage Bin", self.bins[1], f"test-bulk-strategy:{frappe.generate_hash(length=8)}",
            )
            self._make_rule("Bulk")
            result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
            # bins[1] already holds this product (occupied set) and has 8 remaining HU capacity
            # vs bins[0] (also occupied-eligible check first) - only bins[1] is occupied here.
            self.assertEqual(result, self.bins[1])
        finally:
            for bin_name in self.bins:
                frappe.db.set_value("Storage Bin", bin_name, {"maximum_hus": 0, "current_hu_count": 0})

    def test_near_fixed_bin_strategy_prefers_the_products_fixed_bin(self):
        if not frappe.db.exists("WMS Product Warehouse", f"{self.warehouse}-{self.item}"):
            frappe.get_doc({"doctype": "WMS Product Warehouse", "item": self.item, "warehouse": self.warehouse, "fixed_bin": self.bins[1], "active": 1}).insert(ignore_permissions=True)
        else:
            frappe.db.set_value("WMS Product Warehouse", f"{self.warehouse}-{self.item}", "fixed_bin", self.bins[1])
        try:
            self._make_rule("Near Fixed Bin")
            result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
            self.assertEqual(result, self.bins[1])
        finally:
            frappe.db.set_value("WMS Product Warehouse", f"{self.warehouse}-{self.item}", "fixed_bin", None)

    def test_near_fixed_bin_strategy_falls_back_to_bin_sequence_when_no_fixed_bin_configured(self):
        if frappe.db.exists("WMS Product Warehouse", f"{self.warehouse}-{self.item}"):
            frappe.delete_doc("WMS Product Warehouse", f"{self.warehouse}-{self.item}", force=True, ignore_permissions=True)
        self._make_rule("Near Fixed Bin")
        result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        self.assertEqual(result, self.bins[2])

    def test_search_sequence_falls_through_to_next_storage_type_when_first_has_no_room(self):
        # ST (from setUpClass) is full - the sequence must fall through to ST2's bin instead
        # of giving up after the first storage type.
        blocked_bin = f"{self.warehouse}-ST-FULL"
        if not frappe.db.exists("Storage Bin", blocked_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": blocked_bin, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-ST", "active": 1, "putaway_blocked": 1, "sequence": 1}).insert(ignore_permissions=True)
        for bin_name in self.bins:
            frappe.db.set_value("Storage Bin", bin_name, "putaway_blocked", 1)

        if not frappe.db.exists("Storage Type", f"{self.warehouse}-ST2"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": self.warehouse, "storage_type_code": "ST2", "storage_type_name": "ST2", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        st2_bin = f"{self.warehouse}-ST2-BIN"
        if not frappe.db.exists("Storage Bin", st2_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": st2_bin, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-ST2", "active": 1, "sequence": 1}).insert(ignore_permissions=True)

        existing_seq = frappe.get_all("Storage Type Search Sequence", filters={"warehouse": self.warehouse, "direction": "Putaway"}, pluck="name")
        if existing_seq:
            seq_name = existing_seq[0]
        else:
            seq = frappe.get_doc({"doctype": "Storage Type Search Sequence", "warehouse": self.warehouse, "direction": "Putaway", "active": 1,
                "storage_types": [{"storage_type": f"{self.warehouse}-ST", "sequence": 1}, {"storage_type": f"{self.warehouse}-ST2", "sequence": 2}]})
            seq.insert(ignore_permissions=True)
            seq_name = seq.name

        for existing in frappe.get_all("Bin Determination Rule", filters={"warehouse": self.warehouse, "activity": "Putaway"}, pluck="name"):
            frappe.delete_doc("Bin Determination Rule", existing, force=True, ignore_permissions=True)
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
            "search_sequence": seq_name, "strategy": "Bin Sequence"})
        rule.insert(ignore_permissions=True)

        try:
            result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
            self.assertEqual(result, st2_bin)
        finally:
            for bin_name in self.bins:
                frappe.db.set_value("Storage Bin", bin_name, "putaway_blocked", 0)
