import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.api.packing_center import create_hus, move_nodes, packing_materials, packing_tree
from frappe_wms.services.stock import post_entries
from frappe_wms.tests.bootstrap import TEST_ITEM

WH = "PC-TEST-WH"


def qty(**where):
    cond = " and ".join(f"{k}=%({k})s" for k in where)
    return flt(frappe.db.sql(f"select sum(quantity) from `tabWMS Stock Balance` where quantity > 0 and {cond}", where)[0][0])


class TestPackingCenter(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        uom = frappe.db.get_value("Item", TEST_ITEM, "stock_uom")
        if not frappe.db.exists("WMS Product", {"item": TEST_ITEM}):
            frappe.get_doc({"doctype": "WMS Product", "item": TEST_ITEM, "stock_uom": uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Warehouse", WH):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": WH, "warehouse_name": WH, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{WH}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": WH, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage",
                            "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for code in ("A", "B"):
            if not frappe.db.exists("Storage Bin", f"{WH}-{code}"):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{WH}-{code}", "warehouse": WH, "storage_type": f"{WH}-BULK", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        for code, mode in (("PC-PAL", "External"), ("PC-BOX", "Internal")):
            if not frappe.db.exists("Handling Unit Type", code):
                frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": code, "hu_type_name": code, "numbering_mode": mode, "nestable": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Packaging Material", "PC-MAT"):
            frappe.get_doc({"doctype": "Packaging Material", "packaging_material_code": "PC-MAT", "packaging_material_name": "PC carton", "hu_type": "PC-BOX", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Number Range", {"range_for": "Handling Unit", "warehouse": WH}):
            frappe.get_doc({"doctype": "WMS Number Range", "range_for": "Handling Unit", "warehouse": WH, "prefix": "PCT", "start_number": 1, "end_number": 99999,
                            "number_length": 6, "current_number": 0, "active": 1}).insert(ignore_permissions=True)
        cls.uom = uom

    def stock(self, hu, bin_code, quantity):
        post_entries([{"warehouse": WH, "product": TEST_ITEM, "storage_bin": f"{WH}-{bin_code}", "handling_unit": hu, "stock_type": "AVAILABLE",
                       "stock_uom": self.uom, "quantity": quantity, "movement_type": "701"}], "Storage Bin", f"{WH}-{bin_code}", f"pc-seed:{frappe.generate_hash(length=8)}")

    def test_create_uses_packing_material_generates_numbers_and_needs_a_bin(self):
        made = create_hus(WH, f"{WH}-A", packaging_material="PC-MAT", quantity=3)
        self.assertEqual(len(made), 3)
        self.assertEqual(len({h["hu_number"] for h in made}), 3)
        self.assertTrue(all(h["hu_type"] == "PC-BOX" and h["current_bin"] == f"{WH}-A" for h in made))
        nested = create_hus(WH, None, packaging_material="PC-MAT", parent_hu=made[0]["name"])[0]
        self.assertEqual(nested["current_bin"], f"{WH}-A")  # an HU created inside another one is in its bin
        self.assertRaises(frappe.ValidationError, create_hus, WH, None, packaging_material="PC-MAT")  # no bin, no parent
        self.assertRaises(frappe.ValidationError, create_hus, WH, f"{WH}-A", hu_type="PC-PAL", hu_number="PCX1", quantity=2)
        self.assertEqual(create_hus(WH, f"{WH}-A", hu_type="PC-PAL", hu_number=f"PCX{frappe.generate_hash(length=6)}")[0]["hu_type"], "PC-PAL")
        self.assertIn("PC-MAT", [m["name"] for m in packing_materials()])

    def test_tree_nests_hus_and_aggregates_products_then_moves_work(self):
        a, b = create_hus(WH, f"{WH}-A", packaging_material="PC-MAT", quantity=2)
        self.stock(a["name"], "A", 10)
        tree = packing_tree(WH, [f"{WH}-A", f"{WH}-B"])["rows"]
        ids = [r["id"] for r in tree]
        self.assertLess(ids.index(f"bin:{WH}-A"), ids.index(f"hu:{a['name']}"))
        row = next(r for r in tree if r["id"].startswith(f"p:hu:{a['name']}"))
        self.assertEqual((row["kind"], row["quantity"], row["pid"]), ("product", 10, f"hu:{a['name']}"))
        self.assertTrue(next(r for r in tree if r["id"] == f"hu:{a['name']}")["has_kids"])
        self.assertFalse(next(r for r in tree if r["id"] == f"hu:{b['name']}")["has_kids"])

        # 4 of the product: HU a -> HU b (same bin: loose repack)
        line = dict(row["lines"][0], quantity=4)
        res = move_nodes(WH, [{"kind": "stock", "label": "x", "lines": [line]}], "hu", b["name"], "pc-test-1")
        self.assertEqual((res["moved"], res["errors"]), (1, []))
        self.assertEqual((qty(handling_unit=a["name"]), qty(handling_unit=b["name"])), (6, 4))
        # ... and the rest of b to the other bin (a real move task)
        rows = packing_tree(WH, [f"{WH}-A"])["rows"]
        line_b = dict(next(r for r in rows if r["pid"] == f"hu:{b['name']}")["lines"][0])
        res = move_nodes(WH, [{"kind": "stock", "label": "y", "lines": [line_b]}], "bin", f"{WH}-B", "pc-test-2")
        self.assertEqual(res["errors"], [])
        self.assertEqual(qty(storage_bin=f"{WH}-B"), flt(line_b["quantity"]))

        # an HU nests into another, and cannot be packed into itself / its own contents
        res = move_nodes(WH, [{"kind": "hu", "name": b["name"]}], "hu", a["name"], "pc-test-3")
        self.assertEqual(res["moved"], 1)
        self.assertEqual(frappe.db.get_value("Handling Unit", b["name"], "parent_hu"), a["name"])
        res = move_nodes(WH, [{"kind": "hu", "name": a["name"]}], "hu", b["name"], "pc-test-4")
        self.assertEqual(res["moved"], 0)
        self.assertEqual(len(res["errors"]), 1)
        # HU onto a bin relocates it with everything inside
        res = move_nodes(WH, [{"kind": "hu", "name": a["name"]}], "bin", f"{WH}-B", "pc-test-5")
        self.assertEqual(res["errors"], [])
        self.assertEqual(frappe.db.get_value("Handling Unit", a["name"], "current_bin"), f"{WH}-B")
