import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.scanner import resolve_scan
from frappe_wms.services.gs1 import gtin_variants, parse
from frappe_wms.tests.bootstrap import TEST_ITEM


class TestGs1Scan(IntegrationTestCase):
    def test_parser_matches_the_rf_one(self):
        self.assertEqual(parse("0109501101530003172501311012345\x1d3024"),
                         {"gtin": "09501101530003", "expiry": "2025-01-31", "batch": "12345", "count": 24.0})
        self.assertEqual(parse("(00)095011015300000011"), {"sscc": "095011015300000011"})
        self.assertEqual(parse("010950110153000317240200"), {"gtin": "09501101530003", "expiry": "2024-02-29"})
        for code in ("MAD1-BULK-A-01-01", "SKU-00001", "10-A-01", "5901234123457", ""):
            self.assertIsNone(parse(code), code)
        self.assertIn("5901234123457", gtin_variants("05901234123457"))
        self.assertIn("05901234123457", gtin_variants("5901234123457"))

    def test_resolve_scan_finds_items_by_gtin_and_hus_by_sscc(self):
        frappe.set_user("Administrator")
        ean = "4" + frappe.generate_hash(length=12).translate(str.maketrans("abcdef", "123456"))[:12]
        ean = "".join(c for c in ean if c.isdigit())[:13].ljust(13, "7")
        item = frappe.get_doc("Item", TEST_ITEM)
        item.append("barcodes", {"barcode": ean})
        item.save(ignore_permissions=True)
        r = resolve_scan(f"(01)0{ean}(10)LOT-1")
        self.assertEqual([m["name"] for m in r["matches"] if m["type"] == "item"], [TEST_ITEM])
        self.assertEqual(r["gs1"]["batch"], "LOT-1")

        hu = frappe.get_all("Handling Unit", limit=1, pluck="name")
        if hu:
            sscc = "0" + "".join(str((i * 7) % 10) for i in range(17))
            frappe.db.set_value("Handling Unit", hu[0], "sscc", sscc)
            r = resolve_scan(f"00{sscc}")
            self.assertEqual([m["name"] for m in r["matches"] if m["type"] == "hu"], [hu[0]])
