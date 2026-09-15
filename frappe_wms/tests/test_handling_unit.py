import frappe
from frappe.tests import IntegrationTestCase


class TestHandlingUnit(IntegrationTestCase):
    def test_new_handling_unit_can_be_inserted(self):
        warehouse = frappe.get_all("WMS Warehouse", limit=1, pluck="name")
        if not warehouse:
            self.skipTest("no WMS Warehouse configured")
        bin_name = frappe.get_all("Storage Bin", filters={"warehouse": warehouse[0]}, limit=1, pluck="name")
        if not bin_name:
            self.skipTest("no Storage Bin configured for warehouse")
        if not frappe.db.exists("Handling Unit Type", "TEST-HU-TYPE"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-HU-TYPE", "hu_type_name": "Test HU Type"}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-HU-TYPE", "warehouse": warehouse[0], "current_bin": bin_name[0], "status": "Open"})
        hu.insert(ignore_permissions=True)
        self.assertTrue(frappe.db.exists("Handling Unit", hu.name))
