import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task, reverse_task, complete_packing_order, repack


class TestTaskReversalAndPacking(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "REVPACK-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-GR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.stage_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "REVPACK-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "REVPACK-PALLET", "hu_type_name": "Revpack Pallet"}).insert(ignore_permissions=True)

    def _make_hu(self, bin_name):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "REVPACK-PALLET", "warehouse": self.warehouse, "current_bin": bin_name, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def _receive_and_putaway(self, qty):
        hu = self._make_hu(self.recv_bin)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        task = putaway["warehouse_tasks"][0]
        confirm_task(task, confirmed_quantity=qty)
        return hu, task

    def test_reverse_task_moves_stock_and_hu_back(self):
        hu, task = self._receive_and_putaway(15)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), self.bulk_bin)

        result = reverse_task(task, reason="test reversal")
        self.assertEqual(result["status"], "Confirmed")

        bulk_qty = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bulk_bin, "handling_unit": hu.name}, fields=["quantity"])[0].quantity
        recv_qty = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.recv_bin, "handling_unit": hu.name}, fields=["quantity"])[0].quantity
        self.assertEqual(bulk_qty, 0)
        self.assertEqual(recv_qty, 15)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), self.recv_bin)

        reversal_task = frappe.db.get_value("Warehouse Task", {"reversal_of": task}, "name")
        self.assertTrue(reversal_task)
        self.assertEqual(frappe.db.get_value("Warehouse Task", task, "blocking_reason"), "test reversal")

    def test_reverse_task_cannot_run_twice(self):
        hu, task = self._receive_and_putaway(4)
        reverse_task(task)
        with self.assertRaises(frappe.ValidationError):
            reverse_task(task)

    def test_reverse_task_requires_confirmed_task(self):
        hu = self._make_hu(self.recv_bin)
        task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Internal Move", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 5, "stock_uom": self.uom,
            "source_bin": self.recv_bin, "destination_bin": self.bulk_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "301", "priority": "Normal", "status": "Open",
        })
        task.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            reverse_task(task.name)

    def test_complete_packing_order_moves_all_stock_to_destination_hu(self):
        hu, _task = self._receive_and_putaway(9)
        hu2 = self._make_hu(self.bulk_bin)

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 1, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()

        order = frappe.get_doc({"doctype": "Packing Order", "outbound_delivery": obd.name, "work_center_bin": self.bulk_bin,
            "source_hus": [{"handling_unit": hu.name}], "destination_hus": [{"handling_unit": hu2.name}]})
        order.insert(ignore_permissions=True)

        result = complete_packing_order(order.name)
        self.assertEqual(result["status"], "Completed")

        order.reload()
        self.assertEqual(order.status, "Completed")
        self.assertEqual(order.verified_by, "Administrator")
        self.assertTrue(order.completed_at)

        source_qty = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu.name, "storage_bin": self.bulk_bin}, fields=["quantity"])[0].quantity
        dest_qty = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu2.name, "storage_bin": self.bulk_bin}, fields=["quantity"])[0].quantity
        self.assertEqual(source_qty, 0)
        self.assertEqual(dest_qty, 9)

    def test_complete_packing_order_rejects_multi_hu(self):
        hu, _task = self._receive_and_putaway(3)
        hu2 = self._make_hu(self.bulk_bin)
        hu3 = self._make_hu(self.bulk_bin)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 1, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        order = frappe.get_doc({"doctype": "Packing Order", "outbound_delivery": obd.name, "work_center_bin": self.bulk_bin,
            "source_hus": [{"handling_unit": hu.name}], "destination_hus": [{"handling_unit": hu2.name}, {"handling_unit": hu3.name}]})
        order.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            complete_packing_order(order.name)

    def test_repack_without_a_packing_order_references_the_source_hu(self):
        # A standalone RF repack (no Packing Order involved) must not hardcode a reference to a
        # document that doesn't exist - it should reference the Handling Unit itself instead.
        hu, _task = self._receive_and_putaway(9)
        hu2 = self._make_hu(self.bulk_bin)

        repack(hu.name, hu2.name, [{"item": self.item, "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 4}], "test-repack-1")

        source_qty = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu.name, "storage_bin": self.bulk_bin}, fields=["quantity"])[0].quantity
        dest_qty = frappe.get_all("WMS Stock Balance", filters={"handling_unit": hu2.name, "storage_bin": self.bulk_bin}, fields=["quantity"])[0].quantity
        self.assertEqual(source_qty, 5)
        self.assertEqual(dest_qty, 4)

        entry = frappe.get_all("WMS Stock Ledger Entry", filters={"handling_unit": hu2.name, "reference_doctype": "Handling Unit"}, fields=["reference_name"], limit=1)[0]
        self.assertEqual(entry.reference_name, hu.name)

    def test_pick_auto_stages_handling_unit_and_goods_issue_succeeds(self):
        hu, _task = self._receive_and_putaway(8)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=5)

        # FIFO allocation may pick stock from an HU created by an earlier test in this class
        # (IntegrationTestCase only rolls back at class teardown), so assert on whichever HU the
        # pick task actually used rather than assuming it is the one this test created.
        picked_hu = frappe.db.get_value("Warehouse Task", pick_tasks[0], "source_hu")
        self.assertEqual(frappe.db.get_value("Handling Unit", picked_hu, "status"), "Staged")

        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": self.stage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": self.item, "quantity": 5, "stock_uom": self.uom, "handling_unit": picked_hu, "stock_type": "AVAILABLE"}]})
        gi.insert(ignore_permissions=True)
        gi.submit()
        self.assertEqual(gi.status, "Posted")
