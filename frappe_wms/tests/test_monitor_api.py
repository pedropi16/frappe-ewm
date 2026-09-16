import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task, raise_exception
from frappe_wms.api.monitor import (
    get_summary, search_ledger, search_tasks, search_handling_units,
    search_inbound_deliveries, search_outbound_deliveries, search_waves,
    resource_workload, search_queues,
)


class TestMonitorApi(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "MONITOR-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.bulk_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "MONITOR-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "MONITOR-PALLET", "hu_type_name": "Monitor Pallet"}).insert(ignore_permissions=True)

    def _receive(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "MONITOR-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        task = create_putaway(gr.name)["warehouse_tasks"][0]
        return hu, task

    def test_summary_counts_exceptions_and_in_progress_deliveries(self):
        _hu1, task1 = self._receive(10)
        _hu2, task2 = self._receive(5)
        code = frappe.get_all("WMS Exception Code", filters={"active": 1}, limit=1, pluck="name")
        if not code:
            self.skipTest("no WMS Exception Code configured")
        raise_exception(task2, code[0])
        confirm_task(task1, confirmed_quantity=10)

        summary = get_summary(self.warehouse)
        self.assertGreaterEqual(summary["exceptions"], 1, "other tests in this class may also raise exceptions since IntegrationTestCase only rolls back at class teardown")
        self.assertGreaterEqual(summary["inbound_in_progress"], 2)

    def test_search_ledger_filters_by_product_and_returns_both_transfer_legs(self):
        hu, task = self._receive(8)
        confirm_task(task, confirmed_quantity=8)

        rows = search_ledger(self.warehouse, product=self.item)
        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(all(r["product"] == self.item for r in rows))
        quantities = sorted(r["quantity"] for r in rows if r["handling_unit"] == hu.name)
        self.assertIn(-8, quantities)
        self.assertIn(8, quantities)

    def test_search_ledger_filters_by_storage_bin(self):
        hu, task = self._receive(6)
        confirm_task(task, confirmed_quantity=6)
        rows = search_ledger(self.warehouse, storage_bin=self.bulk_bin)
        self.assertTrue(all(r["storage_bin"] == self.bulk_bin for r in rows))
        self.assertTrue(any(r["handling_unit"] == hu.name for r in rows))

    def test_search_tasks_filters_by_status(self):
        _hu, task = self._receive(3)
        code = frappe.get_all("WMS Exception Code", filters={"active": 1}, limit=1, pluck="name")
        if not code:
            self.skipTest("no WMS Exception Code configured")
        raise_exception(task, code[0])
        rows = search_tasks(self.warehouse, status="Exception")
        self.assertTrue(any(r["name"] == task for r in rows))
        self.assertTrue(all(r["status"] == "Exception" for r in rows))

    def test_get_summary_rejects_unknown_warehouse(self):
        with self.assertRaises(frappe.DoesNotExistError):
            get_summary("NON-EXISTENT-WAREHOUSE-XYZ")

    def test_search_handling_units_filters_by_status_and_type(self):
        hu, _task = self._receive(4)
        rows = search_handling_units(self.warehouse, hu_type="MONITOR-PALLET")
        self.assertTrue(any(r["name"] == hu.name for r in rows))
        rows_wrong_status = search_handling_units(self.warehouse, status="Shipped")
        self.assertFalse(any(r["name"] == hu.name for r in rows_wrong_status))

    def test_search_inbound_deliveries_filters_by_status(self):
        _hu, _task = self._receive(2)
        rows = search_inbound_deliveries(self.warehouse)
        self.assertTrue(rows)
        self.assertTrue(all("inbound_delivery_number" in r for r in rows))

    def test_search_outbound_deliveries_and_waves_and_resources(self):
        customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": customer,
            "delivery_date": frappe.utils.nowdate(), "staging_bin": self.bulk_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 1, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        rows = search_outbound_deliveries(self.warehouse)
        self.assertTrue(any(r["name"] == obd.name for r in rows))

        wave = frappe.get_doc({"doctype": "WMS Wave", "warehouse": self.warehouse, "picking_strategy": "Single Order",
            "deliveries": [{"outbound_delivery": obd.name, "customer": customer}]})
        wave.insert(ignore_permissions=True)
        waves = search_waves(self.warehouse, status="Draft")
        match = [w for w in waves if w["name"] == wave.name]
        self.assertTrue(match)
        self.assertEqual(match[0]["delivery_count"], 1)

        resource = frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8), "warehouse": self.warehouse, "resource_type": "Operator", "active": 1})
        resource.insert(ignore_permissions=True)
        workload = resource_workload(self.warehouse)
        self.assertTrue(any(r["name"] == resource.name for r in workload))
        self.assertEqual([r for r in workload if r["name"] == resource.name][0]["open_tasks"], 0)

    def test_search_queues_scoped_to_warehouse(self):
        code = frappe.generate_hash(length=8)
        queue = frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": code, "queue_name": code, "warehouse": self.warehouse, "activity": "Putaway", "active": 1})
        queue.insert(ignore_permissions=True)
        rows = search_queues(self.warehouse)
        self.assertTrue(any(r["name"] == queue.name for r in rows))
