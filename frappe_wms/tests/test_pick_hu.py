import frappe
from frappe.utils import flt

from frappe_wms.services.stock import post_entries
from frappe_wms.services.task import _UNPACK, create_and_confirm_move
from frappe_wms.tests import test_stock_adjustments as _base


class TestPickHU(_base.TestStockAdjustments):
    """A partial move never leaves the moved stock loose: it goes into a Handling Unit created at confirmation (SAP's pick HU)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bin2 = f"{cls.wh}-B2"
        if not frappe.db.exists("Storage Bin", cls.bin2):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin2, "warehouse": cls.wh, "storage_type": f"{cls.wh}-ST", "active": 1, "sequence": 2}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "PICKHU-T"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PICKHU-T", "hu_type_name": "pick hu test"}).insert(ignore_permissions=True)

    def setUp(self):
        super().setUp()
        self.was = frappe.db.get_single_value("WMS Settings", "default_handling_unit_type")
        frappe.db.set_single_value("WMS Settings", "default_handling_unit_type", "PICKHU-T")
        self.addCleanup(lambda: frappe.db.set_single_value("WMS Settings", "default_handling_unit_type", self.was))

    def _move(self, qty, **kw):
        return create_and_confirm_move(warehouse=self.wh, product=self.item, quantity=qty, stock_uom=self.uom, stock_type="AVAILABLE", source_bin=self.bin, destination_bin=self.bin2, **kw)

    def _at(self, bin_):
        return frappe.get_all("WMS Stock Balance", filters={"warehouse": self.wh, "product": self.item, "storage_bin": bin_, "quantity": [">", 0]}, fields=["handling_unit", "quantity"])

    def test_a_partial_move_of_loose_stock_goes_into_a_new_hu(self):
        self._seed(6)
        out = self._move(4)
        rows = self._at(self.bin2)
        self.assertEqual([flt(r.quantity) for r in rows], [4])
        self.assertTrue(rows[0].handling_unit and rows[0].handling_unit == out["destination_hu"])
        self.assertEqual(frappe.db.get_value("Handling Unit", rows[0].handling_unit, "current_bin"), self.bin2)
        self.assertEqual([(r.handling_unit, flt(r.quantity)) for r in self._at(self.bin)], [(None, 2)])

    def test_moving_all_the_loose_stock_or_asking_for_no_hu_stays_loose(self):
        self._seed(6)
        out = self._move(6)
        self.assertFalse(out["destination_hu"])
        self.assertEqual([r.handling_unit for r in self._at(self.bin2)], [None])

    def test_a_partial_move_out_of_an_hu_takes_a_new_hu_and_a_whole_hu_travels_as_it_is(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=8), "hu_type": "PICKHU-T", "warehouse": self.wh, "current_bin": self.bin}).insert(ignore_permissions=True).name
        post_entries([{"warehouse": self.wh, "product": self.item, "storage_bin": self.bin, "handling_unit": hu, "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 6, "movement_type": "701"}],
                     "Storage Bin", self.bin, f"test-pickhu:{frappe.generate_hash(length=8)}")
        out = self._move(4, source_hu=hu)
        self.assertNotEqual(out["destination_hu"], hu)
        self.assertEqual([(r.handling_unit, flt(r.quantity)) for r in self._at(self.bin)], [(hu, 2)])
        out = self._move(2, source_hu=hu)  # what is left is the whole HU: it moves itself
        self.assertEqual(out["destination_hu"], hu)


class TestForeground(_base.TestStockAdjustments):
    """SAP's background / foreground confirmation: what nobody has to choose is confirmed as it is; when only some of the serial numbers (or one of several batches) leave, the user says which."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sitem = frappe.db.get_value("Item", {"is_stock_item": 1, "has_serial_no": 1}, "name")
        cls.bin2 = f"{cls.wh}-B2"
        if not frappe.db.exists("Storage Bin", cls.bin2):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.bin2, "warehouse": cls.wh, "storage_type": f"{cls.wh}-ST", "active": 1, "sequence": 2}).insert(ignore_permissions=True)

    def setUp(self):
        super().setUp()
        if not self.sitem: self.skipTest("no serial-managed item on this site")
        if not frappe.db.exists("WMS Product", {"item": self.sitem}):
            frappe.get_doc({"doctype": "WMS Product", "item": self.sitem, "stock_uom": "Nos", "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        self.sn = [f"FG-SN-{frappe.generate_hash(length=6)}-{i}" for i in range(3)]
        for sn in self.sn:
            if not frappe.db.exists("Serial No", sn): frappe.get_doc({"doctype": "Serial No", "serial_no": sn, "item_code": self.sitem}).insert(ignore_permissions=True)
            post_entries([{"warehouse": self.wh, "product": self.sitem, "serial_no": sn, "storage_bin": self.bin, "stock_type": "AVAILABLE", "stock_uom": "Nos", "quantity": 1, "movement_type": "701"}],
                         "Storage Bin", self.bin, f"test-fg:{sn}")

    def _move(self, qty, **kw):
        return create_and_confirm_move(warehouse=self.wh, product=self.sitem, quantity=qty, stock_uom="Nos", stock_type="AVAILABLE", source_bin=self.bin, destination_bin=self.bin2, **kw)

    def _in(self, bin_):
        return sorted(frappe.get_all("WMS Stock Balance", filters={"warehouse": self.wh, "product": self.sitem, "storage_bin": bin_, "quantity": [">", 0], "serial_no": ["in", self.sn]}, pluck="serial_no"))

    def test_a_partial_move_of_serial_stock_needs_the_serial_numbers(self):
        from frappe_wms.exceptions import ForegroundRequired
        with self.assertRaises(ForegroundRequired):
            self._move(2)
        with self.assertRaisesRegex(ForegroundRequired, "not in"):
            self._move(2, serial_numbers=[self.sn[0], "NOT-HERE"])
        self._move(2, serial_numbers=[self.sn[0], self.sn[2]])
        self.assertEqual(self._in(self.bin), [self.sn[1]])
        self.assertEqual(self._in(self.bin2), sorted([self.sn[0], self.sn[2]]))

    def test_moving_all_the_serial_numbers_needs_no_choice(self):
        self._move(3)
        self.assertEqual(self._in(self.bin2), sorted(self.sn))


class TestSharedPickHU(TestPickHU):
    def test_partial_moves_of_one_warehouse_order_share_its_pick_hu(self):
        from frappe_wms.services.adhoc_tasks import create_adhoc_tasks
        self._seed(6)
        self._seed(6, item=self.item2)
        lines = [{"name": self._line(item=i).name, "quantity": 2} for i in (self.item, self.item2)]
        tasks = create_adhoc_tasks(lines, self.bin2, confirm=1, reason="share")
        hus = {frappe.db.get_value("Warehouse Task", t, "destination_hu") for t in tasks}
        self.assertEqual(len(hus), 1)
        self.assertTrue(hus.pop())
