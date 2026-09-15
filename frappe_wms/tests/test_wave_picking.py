import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task
from frappe_wms.services.picking import release_wave


class TestWavePicking(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WAVE-TEST-WH"
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
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 3}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.stage_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "WAVE-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WAVE-PALLET", "hu_type_name": "Wave Pallet"}).insert(ignore_permissions=True)

    def _receive_and_putaway(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "WAVE-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=qty)
        return hu.name

    def _make_delivery(self, qty):
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        return obd

    def test_cluster_strategy_combines_allocations_from_multiple_deliveries(self):
        hu = self._receive_and_putaway(20)
        obd1 = self._make_delivery(3)
        obd2 = self._make_delivery(4)

        wave = frappe.get_doc({"doctype": "WMS Wave", "warehouse": self.warehouse, "picking_strategy": "Cluster", "priority": "Normal",
            "deliveries": [{"outbound_delivery": obd1.name}, {"outbound_delivery": obd2.name}]})
        wave.insert(ignore_permissions=True)

        tasks = release_wave(wave.name)
        self.assertEqual(len(tasks), 1, "same product/bin/HU/staging-bin allocations should cluster into one task")

        task = frappe.get_doc("Warehouse Task", tasks[0])
        self.assertEqual(task.planned_quantity, 7)
        self.assertEqual(task.wave, wave.name)
        self.assertEqual(len(task.stock_allocations), 2)

        confirm_task(task.name, confirmed_quantity=7)

        allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": ["in", [obd1.name, obd2.name]]}, fields=["status", "picked_quantity"])
        self.assertTrue(all(a.status == "Picked" for a in allocations))

        obd1.reload(); obd2.reload()
        self.assertEqual(obd1.picking_status, "Picked")
        self.assertEqual(obd1.items[0].picked_quantity, 3)
        self.assertEqual(obd2.picking_status, "Picked")
        self.assertEqual(obd2.items[0].picked_quantity, 4)

        balance = frappe.get_all("WMS Stock Balance", filters={"product": self.item, "storage_bin": self.bulk_bin, "handling_unit": hu}, fields=["quantity", "allocated_quantity", "available_quantity"])[0]
        self.assertEqual(balance.quantity, 13)
        self.assertEqual(balance.allocated_quantity, 0, "the whole reservation must release after a single combined confirm")
        self.assertEqual(balance.available_quantity, 13)

        wave.reload()
        self.assertEqual(wave.status, "Released")
        self.assertEqual(wave.released_by, "Administrator")

    def test_single_order_strategy_keeps_one_task_per_allocation(self):
        self._receive_and_putaway(10)
        obd = self._make_delivery(2)
        wave = frappe.get_doc({"doctype": "WMS Wave", "warehouse": self.warehouse, "picking_strategy": "Single Order", "priority": "Normal",
            "deliveries": [{"outbound_delivery": obd.name}]})
        wave.insert(ignore_permissions=True)

        tasks = release_wave(wave.name)
        self.assertEqual(len(tasks), 1)
        task = frappe.get_doc("Warehouse Task", tasks[0])
        self.assertEqual(len(task.stock_allocations), 1)
        self.assertEqual(task.planned_quantity, 2)

    def test_release_wave_requires_draft_status(self):
        self._receive_and_putaway(5)
        obd = self._make_delivery(1)
        wave = frappe.get_doc({"doctype": "WMS Wave", "warehouse": self.warehouse, "picking_strategy": "Single Order", "priority": "Normal",
            "deliveries": [{"outbound_delivery": obd.name}]})
        wave.insert(ignore_permissions=True)
        release_wave(wave.name)
        with self.assertRaises(frappe.ValidationError):
            release_wave(wave.name)
