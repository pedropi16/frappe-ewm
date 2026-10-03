import unittest
import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.labeling import _gs1_check_digit, generate_sscc, render_hu_label_zpl, render_bin_label_zpl


class TestGs1CheckDigit(unittest.TestCase):
    def test_matches_gs1s_own_published_worked_example(self):
        # GS1 General Specifications' own worked example: GTIN body "629104150021" -> check
        # digit 3 (full barcode "6291041500213"). Same Mod-10 algorithm SSCC uses, independently
        # verifiable against a real, citable GS1 reference rather than only self-consistency.
        self.assertEqual(_gs1_check_digit("629104150021"), 3)


class TestSscc(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "SSCC-TEST-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-ST"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "HU Count", "hu_managed": 0, "active": 1}).insert(ignore_permissions=True)
        cls.bin = f"{cls.warehouse}-BIN"
        if not frappe.db.exists("Storage Bin", cls.bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-ST", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "SSCC-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "SSCC-PALLET", "hu_type_name": "SSCC Pallet"}).insert(ignore_permissions=True)

    def _make_hu(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "SSCC-PALLET", "warehouse": self.warehouse, "current_bin": self.bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def test_generate_sscc_is_valid_and_idempotent(self):
        hu = self._make_hu()
        sscc = generate_sscc(hu.name)
        self.assertEqual(len(sscc), 18)
        self.assertTrue(sscc.isdigit())
        # The check digit must validate against the body it was computed from.
        self.assertEqual(_gs1_check_digit(sscc[:17]), int(sscc[17]))
        # Idempotent: calling again returns the same code rather than burning a new serial.
        self.assertEqual(generate_sscc(hu.name), sscc)

    def test_two_handling_units_get_different_sscc(self):
        hu1, hu2 = self._make_hu(), self._make_hu()
        self.assertNotEqual(generate_sscc(hu1.name), generate_sscc(hu2.name))

    def test_render_hu_label_zpl_embeds_the_sscc_and_hu_number(self):
        hu = self._make_hu()
        zpl = render_hu_label_zpl(hu.name)
        self.assertTrue(zpl.startswith("^XA"))
        self.assertTrue(zpl.endswith("^XZ"))
        hu.reload()
        self.assertIn(hu.hu_number, zpl)
        self.assertIn(hu.sscc, zpl)

    def test_render_bin_label_zpl_embeds_the_qr_bin_code_and_check_digits(self):
        zpl = render_bin_label_zpl(self.bin)
        self.assertTrue(zpl.startswith("^XA"))
        self.assertTrue(zpl.endswith("^XZ"))
        self.assertIn("^BQ", zpl)
        self.assertIn(self.bin, zpl)
        check_digits = frappe.db.get_value("Storage Bin", self.bin, "check_digits")
        self.assertIn(check_digits, zpl)
        # The QR's own ^FD payload is the bin code alone - check digits must never ride along
        # inside it, or the whole "you have to physically read it" guarantee is gone.
        qr_payload = zpl.split("^FDQA,")[1].split("^FS")[0]
        self.assertEqual(qr_payload, self.bin)
