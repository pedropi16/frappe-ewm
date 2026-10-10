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


class TestWarehouseOrderSelection(_base.TestStockAdjustments):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.res = frappe.get_all("WMS Resource", filters={"active": 1}, limit=1, pluck="name")

    def _rows(self, *specs):
        return [frappe._dict(name=n, priority=p, latest_start=ls, creation=c) for n, p, ls, c in specs]

    def test_the_queue_offers_priority_then_latest_start_then_age(self):
        from frappe.utils import get_datetime
        from frappe_wms.services.warehouse_order import _by_priority_then_age
        d1, d2 = get_datetime("2026-10-10"), get_datetime("2026-10-12")
        rows = self._rows(("old-no-date", "Normal", None, 1), ("late", "Normal", d2, 3), ("soon", "Normal", d1, 4), ("urgent", "Urgent", None, 5))
        self.assertEqual([r.name for r in _by_priority_then_age(rows)], ["urgent", "soon", "late", "old-no-date"])

    def test_a_skipped_order_goes_back_to_the_queue_and_is_not_offered_again(self):
        from frappe_wms.services.warehouse_order import skip_warehouse_order
        (t1,) = create_adhoc_tasks([{"name": (self._seed(5), self._line().name)[1]}], f"{self.wh}-B2")
        wo = frappe.db.get_value("Warehouse Task", t1, "warehouse_order")
        if not self.res: self.skipTest("no WMS Resource on this site")
        frappe.db.set_value("Warehouse Order", wo, {"status": "Assigned", "assigned_resource": self.res[0]})
        out = skip_warehouse_order(wo)
        self.assertEqual(out["skipped_by"], self.res[0])
        self.assertEqual(frappe.db.get_value("Warehouse Order", wo, ["status", "assigned_resource"]), ("Open", None))
        self.assertIn(self.res[0], frappe.db.get_value("Warehouse Order", wo, "skipped_by").split("\n"))


class TestWaveMergeSimulate(_base.TestStockAdjustments):
    def _delivery(self, qty):
        d = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "customer": frappe.get_all("Customer", limit=1, pluck="name")[0],
                            "delivery_date": frappe.utils.nowdate(), "items": [{"line_number": 1, "item": self.item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        d.submit()
        return d.name

    def _wave(self, *deliveries):
        return frappe.get_doc({"doctype": "WMS Wave", "warehouse": self.wh, "status": "Draft", "deliveries": [{"outbound_delivery": d} for d in deliveries]}).insert(ignore_permissions=True).name

    def test_simulation_shows_the_allocation_and_leaves_nothing_reserved(self):
        from frappe_wms.services.wave import simulate_wave
        self._seed(10)
        d1, d2 = self._delivery(6), self._delivery(6)
        wave = self._wave(d1, d2)
        res = simulate_wave(wave)
        self.assertEqual([(r["delivery"], r["lines"][0]["allocated"]) for r in res], [(d1, 6), (d2, 4)])  # the second one gets what is left
        self.assertEqual(frappe.db.get_value("Outbound Delivery", d1, "allocation_status"), "Not Allocated")
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", self._line().name, "allocated_quantity"), 0)

    def test_draft_waves_merge_into_the_first(self):
        from frappe_wms.services.wave import merge_waves
        w1, w2 = self._wave(self._delivery(1)), self._wave(self._delivery(1))
        with self.assertRaises(frappe.ValidationError): merge_waves([w1])
        out = merge_waves([w2, w1])
        self.assertEqual(out["wave"], min(w1, w2))
        self.assertEqual(out["deliveries"], 2)
        self.assertEqual(len(frappe.get_doc("WMS Wave", out["wave"]).deliveries), 2)
        self.assertFalse(frappe.db.exists("WMS Wave", max(w1, w2)))
