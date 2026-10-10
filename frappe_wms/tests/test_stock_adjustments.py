import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.stock_adjustment import cancel_stock_adjustment, change_stock, create_unplanned_stock, scrap_stock
from frappe_wms.services.stock import post_entries


class TestStockAdjustments(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-ADJ-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1, "has_batch_no": 0, "has_serial_no": 0}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.item2 = "ADJ-TEST-ITEM-2"
        if not frappe.db.exists("Item", cls.item2):
            frappe.get_doc({"doctype": "Item", "item_code": cls.item2, "item_name": cls.item2, "item_group": frappe.db.get_value("Item", cls.item, "item_group"), "stock_uom": frappe.db.get_value("Item", cls.item, "stock_uom"), "is_stock_item": 1}).insert(ignore_permissions=True)
        items = [cls.item, cls.item2]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        for item in items:
            if not frappe.db.exists("WMS Product", {"item": item}):
                frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": frappe.db.get_value("Item", item, "stock_uom"), "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
            frappe.db.set_value("WMS Product", {"item": item}, "warehouse_managed", 1)
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "None",
                            "active": 1, "hu_requirement": "Optional", "allow_mixed_products": 1, "allow_mixed_stock_types": 1}).insert(ignore_permissions=True)
        cls.bin = f"{cls.wh}-B1"
        if not frappe.db.exists("Storage Bin", cls.bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin, "warehouse": cls.wh, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        for code in ("ADJ-OWN",):
            if not frappe.db.exists("WMS Stock Owner", code):
                frappe.get_doc({"doctype": "WMS Stock Owner", "owner_code": code, "owner_name": code, "partner_type": "Other"}).insert(ignore_permissions=True)

    def _seed(self, qty, item=None, **extra):
        item = item or self.item
        post_entries([{"warehouse": self.wh, "product": item, "storage_bin": self.bin, "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701", **extra}],
                     "Storage Bin", self.bin, f"test-adj:{frappe.generate_hash(length=8)}")
        if extra: return  # owner / attribute stock is not mirrored here
        # ERPNext has to hold the same stock, or its Material Issue / Transfer mirrors of the adjustments would run negative
        from frappe_wms.services import erpnext_sync
        se = erpnext_sync._make_stock_entry(stock_entry_type="Material Receipt", company=frappe.db.get_value("WMS Warehouse", self.wh, "company"), remarks="adj test seed")
        se.append("items", {"item_code": item, "qty": qty, "uom": self.uom, "stock_uom": self.uom, "conversion_factor": 1, "t_warehouse": frappe.db.get_value("WMS Warehouse", self.wh, "erpnext_warehouse"),
                            "allow_zero_valuation_rate": 1, **erpnext_sync._dims("AVAILABLE", target=True)})
        se.flags.wms_managed_posting = True
        erpnext_sync._insert_and_submit_as_system(se)

    def _line(self, item=None, stock_type="AVAILABLE", **where):
        filters = {"warehouse": self.wh, "product": item or self.item, "storage_bin": self.bin, "stock_type": stock_type, "quantity": [">", 0], **{k: ["in", ["", None]] for k in ("stock_owner", "country_of_origin", "special_stock_ref")}, **where}
        return frappe.db.get_value("WMS Stock Balance", filters, ["name", "quantity", "available_quantity"], as_dict=True)

    def _qty(self, item=None, stock_type="AVAILABLE", **where):
        plain = {k: ["in", ["", None]] for k in ("stock_owner", "country_of_origin", "special_stock_ref")}
        filters = {"warehouse": self.wh, "product": item or self.item, "storage_bin": self.bin, "stock_type": stock_type, **plain, **where}
        return frappe.db.get_value("WMS Stock Balance", filters, "quantity") or 0

    def setUp(self):
        frappe.db.delete("WMS Stock Balance", {"warehouse": self.wh})
        frappe.db.delete("WMS Stock Ledger Entry", {"warehouse": self.wh})
        frappe.db.delete("WMS Posting Change", {"warehouse": self.wh})
        frappe.db.delete("WMS Stock Adjustment", {"warehouse": self.wh})

    def test_scrapping_takes_free_stock_out_and_can_be_cancelled(self):
        self._seed(10)
        names = scrap_stock([{"name": self._line().name, "quantity": 4}], "broken in the aisle")
        self.assertEqual(self._qty(), 6)
        self.assertEqual(frappe.db.get_value("WMS Stock Adjustment", names[0], "status"), "Posted")
        cancel_stock_adjustment(names[0])
        self.assertEqual(self._qty(), 10)
        with self.assertRaisesRegex(frappe.ValidationError, "reason is required"):
            scrap_stock([{"name": self._line().name}], " ")

    def test_allocated_stock_cannot_be_scrapped_or_changed(self):
        self._seed(10)
        line = self._line()
        frappe.db.set_value("WMS Stock Balance", line.name, {"allocated_quantity": 8, "available_quantity": 2})
        with self.assertRaisesRegex(frappe.ValidationError, "free to scrap"):
            scrap_stock([{"name": line.name, "quantity": 5}], "x")
        with self.assertRaisesRegex(frappe.ValidationError, "free to change"):
            change_stock([{"name": line.name, "quantity": 5}], "x", "WAREHOUSE_BLOCKED")

    def test_unplanned_stock_comes_in_with_its_attributes_and_can_be_cancelled(self):
        name = create_unplanned_stock(self.wh, self.item, self.bin, 7, "found during cleanup", country_of_origin="Germany")
        self.assertEqual(self._qty(country_of_origin="Germany"), 7)
        self.assertEqual(frappe.db.get_value("WMS Stock Ledger Entry", {"reference_name": name}, "movement_type"), "511")
        cancel_stock_adjustment(name)
        self.assertEqual(self._qty(country_of_origin="Germany"), 0)

    def test_posting_change_of_stock_type_owner_attributes_and_product(self):
        self._seed(10)
        change_stock([{"name": self._line().name, "quantity": 3}], "recall hold", "WAREHOUSE_BLOCKED")
        self.assertEqual((self._qty(), self._qty(stock_type="WAREHOUSE_BLOCKED")), (7, 3))
        change_stock([{"name": self._line().name, "quantity": 2}], "sold on consignment", changes={"to_stock_owner": "ADJ-OWN", "to_country_of_origin": "Germany"})
        self.assertEqual(self._qty(stock_owner="ADJ-OWN", country_of_origin="Germany"), 2)
        change_stock([{"name": self._line().name, "quantity": 1}], "reserved for an order", changes={"to_special_stock_type": "Project", "to_special_stock_ref": "PRJ-1"})
        self.assertEqual(self._qty(special_stock_ref="PRJ-1"), 1)
        change_stock([{"name": self._line().name, "quantity": 2}], "relabelled", changes={"to_product": self.item2})
        self.assertEqual(self._qty(item=self.item2), 2)
        self.assertEqual(self._qty(), 2)  # 10 - 3 - 2 - 1 - 2

    def test_a_posting_change_that_changes_nothing_is_refused_and_cancel_reverses(self):
        self._seed(5)
        with self.assertRaisesRegex(frappe.ValidationError, "Nothing changes"):
            change_stock([{"name": self._line().name}], "x", "AVAILABLE")
        name = change_stock([{"name": self._line().name, "quantity": 5}], "hold", "WAREHOUSE_BLOCKED")[0]
        from frappe_wms.services.posting_change import cancel_posting_change
        cancel_posting_change(name)
        self.assertEqual((self._qty(), self._qty(stock_type="WAREHOUSE_BLOCKED")), (5, 0))

    def test_posting_change_worklist_goes_line_by_line(self):
        from frappe_wms.api.posting_change import process_lines
        self._seed(10)
        self._seed(4, item=self.item2)
        a, b = self._line(), self._line(item=self.item2)
        res = process_lines([{"name": a.name, "quantity": 3, "to_stock_type": "WAREHOUSE_BLOCKED", "reason": "hold"}, {"name": b.name, "reason": "nothing changes"}])
        self.assertEqual([c["line"] for c in res["created"]], [0])
        self.assertEqual([e["line"] for e in res["errors"]], [1])
        self.assertEqual(self._qty(stock_type="WAREHOUSE_BLOCKED"), 3)
        self.assertEqual(self._qty(item=self.item2), 4)
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", res["created"][0]["balance"], "stock_type"), "WAREHOUSE_BLOCKED")  # the screen reads the stock left behind

    def _bin2(self):
        name = f"{self.wh}-B2"
        if not frappe.db.exists("Storage Bin", name):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": self.wh, "storage_type": f"{self.wh}-ST", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        return name

    def test_posting_change_check_and_destination(self):
        from frappe_wms.api.posting_change import check_lines, process_lines
        self._seed(10)
        dest = self._bin2()
        line = {"name": self._line().name, "quantity": 4, "to_stock_type": "WAREHOUSE_BLOCKED", "reason": "hold", "destination_bin": dest}
        res = check_lines([line, {"name": self._line().name, "reason": "nothing changes"}, {**line, "destination_section": "NO-SUCH-SECTION"}])
        self.assertEqual([r["ok"] for r in res], [1, 0, 0])
        self.assertIn("WAREHOUSE_BLOCKED", res[0]["summary"])
        self.assertEqual(res[0]["destination_bin"], dest)
        self.assertEqual((self._qty(), self._qty(stock_type="WAREHOUSE_BLOCKED")), (10, 0))  # the check leaves nothing behind
        out = process_lines([{**line, "confirm": 1}])
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["created"][0]["tasks"])
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.wh, "storage_bin": dest, "stock_type": "WAREHOUSE_BLOCKED"}, "quantity"), 4)

    def test_stock_search_by_product_lists_all_its_stock(self):
        from frappe_wms.services.adhoc_tasks import find_rows
        self._seed(3)
        rows = find_rows(self.wh, "stock", "product", self.item)
        self.assertTrue(rows and all(r["product"] == self.item for r in rows))

    def test_scrap_worklist_goes_line_by_line(self):
        from frappe_wms.api.stock_adjustment import process_scrap_lines
        self._seed(10)
        self._seed(4, item=self.item2)
        a, b = self._line(), self._line(item=self.item2)
        res = process_scrap_lines([{"name": a.name, "quantity": 3, "reason": "broken"}, {"name": b.name, "quantity": 2, "reason": " "}])
        self.assertEqual([c["line"] for c in res["created"]], [0])
        self.assertEqual([e["line"] for e in res["errors"]], [1])
        self.assertIn("reason", res["errors"][0]["error"])
        self.assertEqual(self._qty(), 7)
        self.assertEqual(self._qty(item=self.item2), 4)
