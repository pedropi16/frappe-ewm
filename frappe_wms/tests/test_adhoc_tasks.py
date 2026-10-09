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
