import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.determination import determine_destination_bin


class TestStorageStructure(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "STORSTRUCT-TEST-WH"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)

    def test_storage_section_creation_and_autoname(self):
        if not frappe.db.exists("Storage Section", f"{self.warehouse}-ST-FAST"):
            section = frappe.get_doc({"doctype": "Storage Section", "storage_type": f"{self.warehouse}-ST", "section_code": "FAST", "section_name": "Fast Movers", "active": 1})
            section.insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Storage Section", f"{self.warehouse}-ST-FAST"))

    def test_bin_type_creation_and_autoname(self):
        if not frappe.db.exists("Bin Type", "PALLET-STD"):
            bin_type = frappe.get_doc({"doctype": "Bin Type", "bin_type_code": "PALLET-STD", "bin_type_name": "Standard Pallet", "maximum_weight": 500, "maximum_volume": 1.2})
            bin_type.insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Bin Type", "PALLET-STD"))

    def test_storage_bin_accepts_section_and_bin_type(self):
        if not frappe.db.exists("Storage Section", f"{self.warehouse}-ST-SLOW"):
            frappe.get_doc({"doctype": "Storage Section", "storage_type": f"{self.warehouse}-ST", "section_code": "SLOW", "section_name": "Slow Movers", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Type", "SHELF-STD"):
            frappe.get_doc({"doctype": "Bin Type", "bin_type_code": "SHELF-STD", "bin_type_name": "Standard Shelf"}).insert(ignore_permissions=True)
        bin_name = f"{self.warehouse}-SECTIONED-BIN"
        if not frappe.db.exists("Storage Bin", bin_name):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-ST",
                "storage_section": f"{self.warehouse}-ST-SLOW", "bin_type": "SHELF-STD", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        bin_doc = frappe.get_doc("Storage Bin", bin_name)
        self.assertEqual(bin_doc.storage_section, f"{self.warehouse}-ST-SLOW")
        self.assertEqual(bin_doc.bin_type, "SHELF-STD")

    def test_bin_determination_rule_restricts_candidates_to_destination_section(self):
        # Two bins in the same storage type but different sections - a rule with
        # destination_section set must only ever pick from its own section.
        for code, name in (("A", "Section A"), ("B", "Section B")):
            if not frappe.db.exists("Storage Section", f"{self.warehouse}-ST-{code}"):
                frappe.get_doc({"doctype": "Storage Section", "storage_type": f"{self.warehouse}-ST", "section_code": code, "section_name": name, "active": 1}).insert(ignore_permissions=True)
        bin_a = f"{self.warehouse}-SEC-A-BIN"
        bin_b = f"{self.warehouse}-SEC-B-BIN"
        for bin_name, section, seq in ((bin_a, f"{self.warehouse}-ST-A", 1), (bin_b, f"{self.warehouse}-ST-B", 2)):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": self.warehouse, "storage_type": f"{self.warehouse}-ST",
                    "storage_section": section, "active": 1, "sequence": seq}).insert(ignore_permissions=True)

        for existing in frappe.get_all("Bin Determination Rule", filters={"warehouse": self.warehouse, "activity": "Putaway"}, pluck="name"):
            frappe.delete_doc("Bin Determination Rule", existing, force=True, ignore_permissions=True)
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.warehouse, "activity": "Putaway", "active": 1, "priority": 1,
            "destination_storage_type": f"{self.warehouse}-ST", "destination_section": f"{self.warehouse}-ST-B", "strategy": "Bin Sequence"})
        rule.insert(ignore_permissions=True)

        result = determine_destination_bin({"warehouse": self.warehouse, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "source_storage_type": None, "destination_hu": "DUMMY-HU"})
        self.assertEqual(result, bin_b)
