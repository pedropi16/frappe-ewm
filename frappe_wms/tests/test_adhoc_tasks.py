import frappe

from frappe_wms.api.adhoc import create_adhoc_tasks
from frappe_wms.services.task import confirm_task
from frappe_wms.tests import test_stock_adjustments as _base


class TestAdhocTasks(_base.TestStockAdjustments):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bin2 = f"{cls.wh}-B2"
        if not frappe.db.exists("Storage Bin", cls.bin2):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin2, "warehouse": cls.wh, "storage_type": f"{cls.wh}-ST", "active": 1, "sequence": 2}).insert(ignore_permissions=True)

    def test_several_stock_lines_become_one_batch_of_tasks(self):
        self._seed(10)
        self._seed(5, item=self.item2)
        lines = [{"name": self._line().name, "quantity": 4}, {"name": self._line(item=self.item2).name}]
        tasks = create_adhoc_tasks(lines, self.bin2)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len({frappe.db.get_value("Warehouse Task", t, "warehouse_order") for t in tasks}), 1)
        for t in tasks: confirm_task(t, scanned_source=None, scanned_destination=None, verify=False)
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.wh, "product": self.item, "storage_bin": self.bin2, "stock_type": "AVAILABLE"}, "quantity"), 4)
        self.assertEqual(self._qty(), 6)
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.wh, "product": self.item2, "storage_bin": self.bin2}, "quantity"), 5)

    def test_handling_unit_line_moves_the_unit(self):
        from frappe_wms.services.handling_unit import create_handling_unit
        hu_type = frappe.get_all("Handling Unit Type", limit=1, pluck="name")[0]
        hu = create_handling_unit(f"ADHOC-{frappe.generate_hash(length=6)}", hu_type, self.bin, warehouse=self.wh)
        hu = hu.name if hasattr(hu, "name") else hu
        self._seed(7, handling_unit=hu)
        tasks = create_adhoc_tasks([{"handling_unit": hu}], self.bin2)
        self.assertEqual(len(tasks), 1)
        confirm_task(tasks[0], verify=False)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu, "current_bin"), self.bin2)

    def test_destination_in_another_warehouse_is_refused(self):
        self._seed(3)
        other = frappe.get_all("Storage Bin", filters={"warehouse": ["!=", self.wh]}, limit=1, pluck="name")[0]
        with self.assertRaisesRegex(frappe.ValidationError, "not in warehouse"):
            create_adhoc_tasks([{"name": self._line().name}], other)

    def test_mixed_handling_unit_moves_with_all_its_stock(self):
        from frappe_wms.services.handling_unit import create_handling_unit
        hu_type = frappe.get_all("Handling Unit Type", limit=1, pluck="name")[0]
        hu = create_handling_unit(f"ADHOC-{frappe.generate_hash(length=6)}", hu_type, self.bin, warehouse=self.wh)
        hu = hu.name if hasattr(hu, "name") else hu
        self._seed(7, handling_unit=hu)
        self._seed(2, item=self.item2, handling_unit=hu)
        tasks = create_adhoc_tasks([{"handling_unit": hu}], self.bin2)
        self.assertEqual(len(tasks), 2)
        for t in tasks: confirm_task(t, verify=False)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu, "current_bin"), self.bin2)
        self.assertEqual(self._qty(item=self.item2, handling_unit=hu), 0)
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.wh, "storage_bin": self.bin2, "handling_unit": hu, "quantity": [">", 0]}, "name") is not None, True)

    def test_process_type_reason_and_immediate_confirmation(self):
        self._seed(10)
        tasks = create_adhoc_tasks([{"name": self._line().name, "quantity": 6}], self.bin2, "High", "INTERNAL_MOVE", "re-slotting", 1)
        row = frappe.db.get_value("Warehouse Task", tasks[0], ["status", "reason", "priority", "movement_type"], as_dict=True)
        self.assertEqual((row.status, row.reason, row.priority), ("Confirmed", "re-slotting", "High"))
        self.assertEqual(self._qty(), 4)
        with self.assertRaisesRegex(frappe.ValidationError, "not an ad hoc movement"):
            create_adhoc_tasks([{"name": self._line().name}], self.bin2, "Normal", "OB_PICK")

    def test_worklist_find_and_per_line_create(self):
        from frappe_wms.api.adhoc import find_rows, process_lines, task_status
        self._seed(10)
        self._seed(5, item=self.item2)
        rows = find_rows(self.wh, "stock", "storage_bin", self.bin)
        self.assertEqual({r["product"] for r in rows}, {self.item, self.item2})
        self.assertEqual(find_rows(self.wh, "stock", "product", self.item2)[0]["source_bin"], self.bin)
        lines = [{"name": r["name"], "quantity": 3, "destination_bin": self.bin2} for r in rows] + [{"name": rows[0]["name"], "quantity": 1}]  # the last has no destination
        res = process_lines(lines, {"reason": "worklist"})
        self.assertEqual([c["line"] for c in res["created"]], [0, 1])
        self.assertEqual([e["line"] for e in res["errors"]], [2])
        self.assertIn("destination", res["errors"][0]["error"])
        self.assertEqual({t["reason"] for t in task_status([n for c in res["created"] for n in c["tasks"]])}, {"worklist"})
        self.assertEqual(len({frappe.db.get_value("Warehouse Task", n, "warehouse_order") for c in res["created"] for n in c["tasks"]}), 1)

    def test_worklist_hu_rows_show_bin_open_tasks_and_content(self):
        from frappe_wms.api.adhoc import find_rows, hu_content, hu_master
        from frappe_wms.services.handling_unit import create_handling_unit
        hu_type = frappe.get_all("Handling Unit Type", limit=1, pluck="name")[0]
        hu = create_handling_unit(f"WL-{frappe.generate_hash(length=6)}", hu_type, self.bin, warehouse=self.wh)
        hu = hu.name if hasattr(hu, "name") else hu
        self._seed(7, handling_unit=hu)
        row = find_rows(self.wh, "hu", "handling_unit", hu)[0]
        self.assertEqual((row["source_bin"], row["open_wt"], row["top_hu"]), (self.bin, 0, hu))
        create_adhoc_tasks([{"handling_unit": hu}], self.bin2)
        self.assertEqual(find_rows(self.wh, "hu", "storage_bin", self.bin)[0]["open_wt"], 1)
        self.assertEqual(hu_content(hu)[0]["quantity"], 7)
        self.assertEqual(hu_master(hu)["current_bin"], self.bin)

    def test_no_hu_wt_unpacks_the_content_loose(self):
        from frappe_wms.api.adhoc import process_lines
        from frappe_wms.services.handling_unit import create_handling_unit
        hu_type = frappe.get_all("Handling Unit Type", limit=1, pluck="name")[0]
        hu = create_handling_unit(f"UNP-{frappe.generate_hash(length=6)}", hu_type, self.bin, warehouse=self.wh)
        hu = hu.name if hasattr(hu, "name") else hu
        self._seed(6, handling_unit=hu)
        res = process_lines([{"handling_unit": hu, "destination_bin": self.bin2, "unpack": 1, "confirm": 1}])
        self.assertEqual(res["errors"], [])
        self.assertEqual(frappe.db.get_value("Handling Unit", hu, "current_bin"), self.bin)  # the HU stays, its stock went loose
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.wh, "storage_bin": self.bin2, "handling_unit": ["in", ["", None]], "quantity": [">", 0]}, "quantity"), 6)

    def test_enter_resolves_destination_type_section_and_refuses_mismatches(self):
        from frappe_wms.api.adhoc import check_lines, process_lines
        self._seed(10)
        line = {"name": self._line().name, "quantity": 2}
        r = check_lines([{**line, "destination_bin": self.bin2}])[0]
        self.assertEqual((r["ok"], r["destination_bin"], r["destination_storage_type"]), (1, self.bin2, f"{self.wh}-ST"))
        self.assertTrue(r["process_type"])
        bad = check_lines([{**line, "destination_bin": self.bin2, "destination_storage_type": "NOT-THIS-TYPE"}])[0]
        self.assertEqual(bad["ok"], 0)
        self.assertIn("storage type", bad["error"])
        before = frappe.db.count("Warehouse Task", {"warehouse": self.wh})
        none = check_lines([line])[0]  # no rule, no indicators: nothing can be determined
        self.assertEqual(none["ok"], 0)
        self.assertIn("destination", none["error"])
        self.assertEqual(frappe.db.count("Warehouse Task", {"warehouse": self.wh}), before)  # a check creates nothing

    def test_a_bin_determination_rule_picks_the_destination_when_none_is_entered(self):
        from frappe_wms.api.adhoc import check_lines, process_lines
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.wh, "activity": "Internal Move", "priority": 1, "active": 1, "strategy": "Bin Sequence",
                               "destination_storage_type": f"{self.wh}-ST"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: frappe.delete_doc("Bin Determination Rule", rule.name, force=1, ignore_permissions=True))
        self._seed(10)
        line = {"name": self._line().name, "quantity": 2}
        before = frappe.db.count("Warehouse Task", {"warehouse": self.wh})
        r = check_lines([line])[0]
        self.assertEqual(r["ok"], 1, r)
        self.assertEqual(frappe.db.count("Warehouse Task", {"warehouse": self.wh}), before)
        res = process_lines([line])
        self.assertEqual(res["errors"], [])
        self.assertEqual(res["created"][0]["destination_bin"], r["destination_bin"])
        self.assertEqual(frappe.db.get_value("Warehouse Task", res["created"][0]["tasks"][0], "destination_bin"), r["destination_bin"])
