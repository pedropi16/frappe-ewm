import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.selection import (
    delete_variant, execute_selection, get_selection_screen, list_variants, save_variant, set_default_variant,
)
from frappe_wms.services.selection import compile_selection, run_selection

WH = "SEL-TEST-WH"


def inc(*rows):
    return {"include": list(rows)}


class TestSelectionScreen(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", WH):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": WH, "warehouse_name": WH, "company": company,
                            "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in (("GR", "Receiving"), ("BULK", "Storage")):
            if not frappe.db.exists("Storage Type", f"{WH}-{code}"):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": WH, "storage_type_code": code, "storage_type_name": code,
                                "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        # 12 bins: SEL-TEST-WH-A-01..06 in BULK (sequence 1..6), SEL-TEST-WH-B-01..06 in GR (sequence 11..16)
        for aisle, st, base in (("A", "BULK", 0), ("B", "GR", 10)):
            for i in range(1, 7):
                code = f"{WH}-{aisle}-{i:02d}"
                if not frappe.db.exists("Storage Bin", code):
                    frappe.get_doc({"doctype": "Storage Bin", "bin_code": code, "warehouse": WH, "storage_type": f"{WH}-{st}",
                                    "active": 1, "sequence": base + i}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "SEL-PAL"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "SEL-PAL", "hu_type_name": "Selection Pallet"}).insert(ignore_permissions=True)
        cls.hus = []
        for i, bin_code in enumerate((f"{WH}-A-01", f"{WH}-A-02", f"{WH}-B-01")):
            number = f"SELHU{i:03d}"
            name = frappe.db.get_value("Handling Unit", {"hu_number": number}) or frappe.get_doc({
                "doctype": "Handling Unit", "hu_number": number, "hu_type": "SEL-PAL", "warehouse": WH,
                "current_bin": bin_code, "status": "Open"}).insert(ignore_permissions=True).name
            cls.hus.append(name)
        frappe.db.set_value("Handling Unit", cls.hus[0], "external_reference", "50%_OFF")

    def bins(self, criteria):
        rows = run_selection("Storage Bin", criteria, ["name"], base_filters={"warehouse": WH}, order_by="name asc", max_hits=100)
        return [r.name for r in rows]

    def test_single_values_patterns_and_ranges(self):
        self.assertEqual(self.bins({"name": inc({"op": "eq", "low": f"{WH}-A-03"})}), [f"{WH}-A-03"])
        # '*' in an "eq" value is a pattern, as in SAP
        self.assertEqual(len(self.bins({"name": inc({"op": "eq", "low": f"{WH}-B*"})})), 6)
        self.assertEqual(self.bins({"name": inc({"op": "cp", "low": f"*-A-0+"})})[:2], [f"{WH}-A-01", f"{WH}-A-02"])
        self.assertEqual(self.bins({"sequence": inc({"op": "bt", "low": 3, "high": 5})}), [f"{WH}-A-03", f"{WH}-A-04", f"{WH}-A-05"])
        self.assertEqual(len(self.bins({"sequence": inc({"op": "ge", "low": 14})})), 3)
        self.assertEqual(len(self.bins({"sequence": inc({"op": "lt", "low": 3})})), 2)
        self.assertEqual(len(self.bins({"sequence": inc({"op": "nb", "low": 2, "high": 15})})), 2)  # 1 and 16

    def test_include_or_exclude_and_fields_and(self):
        crit = {"name": {"include": [{"op": "cp", "low": f"{WH}-A*"}, {"op": "eq", "low": f"{WH}-B-06"}],
                         "exclude": [{"op": "bt", "low": f"{WH}-A-02", "high": f"{WH}-A-04"}]}}
        self.assertEqual(self.bins(crit), [f"{WH}-A-01", f"{WH}-A-05", f"{WH}-A-06", f"{WH}-B-06"])
        # a second field narrows (AND) - only aisle A bins are BULK
        crit["storage_type"] = inc({"op": "eq", "low": f"{WH}-GR"})
        self.assertEqual(self.bins(crit), [f"{WH}-B-06"])
        # exclude-only selection means "everything except"
        self.assertEqual(len(self.bins({"name": {"exclude": [{"op": "cp", "low": f"{WH}-A*"}]}})), 6)

    def test_pasted_list_becomes_in_and_literal_like_chars_are_escaped(self):
        pasted = [{"op": "eq", "low": f"{WH}-A-0{i}"} for i in range(1, 7)] + [{"op": "eq", "low": "NOT-A-BIN"}]
        cond = compile_selection("Storage Bin", {"name": {"include": pasted}})
        self.assertIn(" in (", cond)
        self.assertEqual(len(self.bins({"name": {"include": pasted}})), 6)
        # '%' and '_' typed by the user are literal characters, not SQL wildcards
        self.assertEqual(self.bins({"name": inc({"op": "cp", "low": "SEL%TEST*"})}), [])
        self.assertEqual(self.bins({"name": inc({"op": "cp", "low": "SEL_TEST*"})}), [])
        hu = lambda op, low: [r.name for r in run_selection("Handling Unit", {"external_reference": inc({"op": op, "low": low})}, ["name"], base_filters={"warehouse": WH})]  # noqa: E731
        self.assertEqual(hu("eq", "50%_OFF"), [self.hus[0]])
        self.assertEqual(hu("cp", "50%_*"), [self.hus[0]])
        self.assertEqual(hu("cp", "5+%*"), [self.hus[0]])
        self.assertEqual(hu("eq", "50xxOFF"), [])

    def test_blank_matches_null_and_ne_keeps_blank_rows(self):
        # parent_hu is empty on all three HUs: "= blank" finds them, "<> X" must not drop them
        rows = run_selection("Handling Unit", {"parent_hu": inc({"op": "eq", "low": ""})}, ["name"], base_filters={"warehouse": WH})
        self.assertEqual(len(rows), 3)
        rows = run_selection("Handling Unit", {"parent_hu": inc({"op": "ne", "low": "SOMETHING"})}, ["name"], base_filters={"warehouse": WH})
        self.assertEqual(len(rows), 3)

    def test_datetime_field_with_a_date_means_the_whole_day(self):
        today = frappe.utils.today()
        rows = run_selection("Handling Unit", {"creation": inc({"op": "eq", "low": today})}, ["name"], base_filters={"warehouse": WH})
        self.assertEqual(len(rows), 3)
        rows = run_selection("Handling Unit", {"creation": inc({"op": "gt", "low": today})}, ["name"], base_filters={"warehouse": WH})
        self.assertEqual(rows, [])

    def test_rejects_unknown_fields_and_operators(self):
        with self.assertRaises(frappe.ValidationError):
            compile_selection("Storage Bin", {"no_such_field": inc({"op": "eq", "low": "x"})})
        with self.assertRaises(frappe.ValidationError):
            compile_selection("Storage Bin", {"name": inc({"op": "drop table", "low": "x"})})
        # a value is always escaped - never executed
        self.assertEqual(self.bins({"name": inc({"op": "eq", "low": "x' or '1'='1"})}), [])

    def test_monitor_view_virtual_fields(self):
        screen = get_selection_screen("hu")
        fields = {f["fieldname"] for f in screen["fields"]}
        self.assertTrue({"hu_number", "storage_type", "work_center", "contains_product"} <= fields)
        res = execute_selection("hu", WH, {"storage_type": inc({"op": "eq", "low": f"{WH}-BULK"})}, ["hu_number", "current_bin"])
        self.assertEqual(sorted(r["hu_number"] for r in res["rows"]), ["SELHU000", "SELHU001"])
        res = execute_selection("hu", WH, {"storage_type": {"exclude": [{"op": "eq", "low": f"{WH}-BULK"}]}}, None, max_hits=1)
        self.assertEqual(len(res["rows"]), 1)
        self.assertTrue(res["truncated"])
        # Paging: page by page gives every hit exactly once, in the same order as one big page.
        everything = [r["name"] for r in execute_selection("hu", WH, {}, ["hu_number"], max_hits=500)["rows"]]
        paged, start = [], 0
        while True:
            page = execute_selection("hu", WH, {}, ["hu_number"], max_hits=2, start=start)
            paged += [r["name"] for r in page["rows"]]
            start += len(page["rows"])
            if not page["truncated"]: break
        self.assertEqual(paged, everything)

    def test_stock_view_adds_storage_type_and_grouping_columns(self):
        item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        bal = frappe.get_doc({"doctype": "WMS Stock Balance", "name": frappe.generate_hash(length=20), "warehouse": WH, "product": item, "storage_bin": f"{WH}-A-01",
                              "stock_type": "AVAILABLE", "quantity": 3, "available_quantity": 3,
                              "stock_uom": frappe.db.get_value("Item", item, "stock_uom")}).insert(ignore_permissions=True)
        res = execute_selection("stock", WH, {}, ["product"])  # a layout that hid everything else
        row = next(r for r in res["rows"] if r["name"] == bal.name)
        self.assertEqual(row["storage_type"], f"{WH}-BULK")
        self.assertEqual(row["allocs"], [])
        self.assertEqual((row["parent_hu"], row["top_hu"]), ("", ""))
        self.assertTrue({"storage_bin", "handling_unit", "serial_no", "allocated_quantity", "first_receipt_date"} <= set(row))

    def test_variants_default_and_global_visibility(self):
        view = "hu"
        for v in list_variants(view):
            if v["mine"]:
                delete_variant(v["name"])
        a = save_variant(view, "Selection", "Bulk only", criteria={"storage_type": inc({"op": "eq", "low": f"{WH}-BULK"})}, is_default=1)
        b = save_variant(view, "Selection", "Everything", criteria={}, is_default=1)
        variants = {v["name"]: v for v in list_variants(view)}
        self.assertEqual(variants[a]["is_default"], 0)  # saving b as default cleared a
        self.assertEqual(variants[b]["is_default"], 1)
        self.assertEqual(variants[a]["criteria"]["storage_type"]["include"][0]["low"], f"{WH}-BULK")
        # saving under the same name overwrites instead of duplicating
        self.assertEqual(save_variant(view, "Selection", "Bulk only", criteria={}), a)
        set_default_variant(view, "Selection", a)
        self.assertEqual({v["name"]: v["is_default"] for v in list_variants(view)}[a], 1)
        lay = save_variant(view, "Layout", "Narrow", layout={"columns": ["hu_number", "current_bin"], "sort": ["current_bin", 1]}, is_global=1)

        user = "sel-operator@example.test"
        if not frappe.db.exists("User", user):
            u = frappe.get_doc({"doctype": "User", "email": user, "first_name": "Sel", "send_welcome_email": 0})
            u.insert(ignore_permissions=True)
            u.add_roles("WMS Operator")
        frappe.set_user(user)
        try:
            names = {v["name"] for v in list_variants(view)}
            self.assertIn(lay, names)       # global layout is shared
            self.assertNotIn(a, names)      # personal selection variants are not
            with self.assertRaises(frappe.PermissionError):
                save_variant(view, "Selection", "Mine global", criteria={}, is_global=1)
            with self.assertRaises(frappe.PermissionError):
                delete_variant(lay)
        finally:
            frappe.set_user("Administrator")
