import frappe

from frappe_wms.api.adhoc import create_adhoc_tasks
from frappe_wms.services.background_confirm import confirm_in_background
from frappe_wms.tests import test_stock_adjustments as _base


class TestBackgroundConfirm(_base.TestStockAdjustments):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bin2 = f"{cls.wh}-B2"
        if not frappe.db.exists("Storage Bin", cls.bin2):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin2, "warehouse": cls.wh, "storage_type": f"{cls.wh}-ST", "active": 1, "sequence": 2}).insert(ignore_permissions=True)

    def _open_tasks(self, *items):
        for i, item in enumerate(items): self._seed(5 + i, item=item)
        return create_adhoc_tasks([{"name": self._line(item=item).name} for item in items], self.bin2)

    def test_tasks_and_orders_are_confirmed_in_the_background(self):
        (t1,) = self._open_tasks(self.item)
        res = confirm_in_background("Warehouse Task", [t1, "NO-SUCH-TASK"])
        self.assertEqual([d["name"] for d in res["done"]], [t1])
        self.assertEqual([e["name"] for e in res["errors"]], ["NO-SUCH-TASK"])
        self.assertEqual(frappe.db.get_value("Warehouse Task", t1, "status"), "Confirmed")
        self.assertEqual(self._qty(), 0)  # left the source bin
        self.assertEqual(frappe.db.get_value("Warehouse Task", t1, "confirmed_quantity"), 5)
        again = confirm_in_background("Warehouse Task", [t1])
        self.assertEqual(again["done"], [])
        self.assertIn("Nothing left", again["errors"][0]["error"])

        t2, t3 = self._open_tasks(self.item, self.item2)
        order = frappe.db.get_value("Warehouse Task", t2, "warehouse_order")
        self.assertTrue(order)
        res = confirm_in_background("Warehouse Order", [order])
        self.assertEqual(res["errors"], [])
        self.assertEqual(frappe.db.get_value("Warehouse Task", t2, "status"), "Confirmed")
        self.assertEqual(frappe.db.get_value("Warehouse Task", t3, "status"), "Confirmed")

    def test_a_task_that_needs_details_stays_open_for_the_foreground(self):
        from unittest import mock
        (t1,) = self._open_tasks(self.item)
        with mock.patch("frappe_wms.services.task.details_needed", return_value={"serial": ["S1", "S2"]}):
            res = confirm_in_background("Warehouse Task", [t1])
        self.assertEqual(res["foreground"], [t1])
        self.assertIn("foreground", res["done"][0]["text"])
        self.assertNotEqual(frappe.db.get_value("Warehouse Task", t1, "status"), "Confirmed")
