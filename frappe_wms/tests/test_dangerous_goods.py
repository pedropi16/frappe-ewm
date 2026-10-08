import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.bin_rules import bin_violations


class TestDangerousGoods(IntegrationTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        self.items = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=2, pluck="name")
        for it in self.items:
            if not frappe.db.exists("WMS Product", it):
                frappe.get_doc({"doctype": "WMS Product", "item": it, "stock_uom": frappe.db.get_value("Item", it, "stock_uom"), "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        for code in ("DG-3", "DG-5"):
            frappe.db.delete("Hazard Class", {"name": code})
        frappe.get_doc({"doctype": "Hazard Class", "class_code": "DG-5"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Hazard Class", "class_code": "DG-3", "description": "Flammable liquids", "incompatible_classes": [{"hazard_class": "DG-5"}]}).insert(ignore_permissions=True)
        self.wh = f"DG-{frappe.generate_hash(length=5).upper()}"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": self.wh, "warehouse_name": self.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        self.types = {}
        for code in ("PLAIN", "FLAM"):
            doc = {"doctype": "Storage Type", "warehouse": self.wh, "storage_type_code": code, "storage_type_name": code, "storage_role": "Storage",
                   "capacity_check_method": "HU Count", "hu_managed": 0, "active": 1}
            if code == "FLAM": doc.update(allowed_hazard_classes=[{"hazard_class": "DG-3"}, {"hazard_class": "DG-5"}], dg_points_limit=10)
            self.types[code] = frappe.get_doc(doc).insert(ignore_permissions=True).name
        self.bins = {c: frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{self.wh}-{c}", "warehouse": self.wh, "storage_type": t, "active": 1, "sequence": 1}).insert(ignore_permissions=True).name
                     for c, t in self.types.items()}
        frappe.db.set_value("WMS Product", self.items[0], {"hazard_class": "DG-3", "dg_points_per_unit": 3})
        frappe.db.set_value("WMS Product", self.items[1], {"hazard_class": "DG-5", "dg_points_per_unit": 1})

    def test_off_by_default(self):
        self.assertEqual(bin_violations(self.bins["PLAIN"], item=self.items[0], incoming_quantity=1, require_hu=False), [])

    def test_class_permission_segregation_and_points(self):
        frappe.db.set_value("WMS Warehouse", self.wh, "dangerous_goods_check", 1)
        check = lambda b, i, q=1: bin_violations(self.bins[b], item=self.items[i], incoming_quantity=q, require_hu=False)
        self.assertRegex(" ".join(check("PLAIN", 0)), "not allowed in storage type")
        self.assertEqual(check("FLAM", 0, 3), [])
        self.assertRegex(" ".join(check("FLAM", 0, 4)), "exceed the limit")  # 4 x 3 = 12 > 10
        frappe.get_doc({"doctype": "WMS Stock Balance", "__newname": frappe.generate_hash(length=10), "product": self.items[1], "storage_bin": self.bins["FLAM"], "warehouse": self.wh,
                        "stock_type": "AVAILABLE", "stock_uom": frappe.db.get_value("Item", self.items[1], "stock_uom"), "quantity": 1, "available_quantity": 1}).insert(ignore_permissions=True)
        self.assertRegex(" ".join(check("FLAM", 0)), "must not be stored with DG-5")
