import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import list_open_inbound_deliveries, create_and_submit_goods_receipt
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks, list_ready_to_ship, create_and_submit_goods_issue
from frappe_wms.api.scanner import confirm_task, create_and_confirm_move, list_open_packing_orders, complete_packing_order
from frappe_wms.api.inventory import list_open_counts, list_open_inspections


class TestRfAppParity(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "RFPARITY-TEST-WH"
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
        if not frappe.db.exists("Handling Unit Type", "RFPARITY-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "RFPARITY-PALLET", "hu_type_name": "RF Parity Pallet"}).insert(ignore_permissions=True)

    def _receive_and_putaway(self, qty, hu_number=None):
        hu_number = hu_number or frappe.generate_hash(length=10)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        result = create_and_submit_goods_receipt(ind.name, [
            {"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu_number, "stock_type": "AVAILABLE", "hu_type": "RFPARITY-PALLET"},
        ])
        confirm_task(result["warehouse_tasks"][0], confirmed_quantity=qty)
        return ind, hu_number

    def test_list_open_inbound_deliveries_shows_unreceived_delivery(self):
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 4, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        rows = list_open_inbound_deliveries()
        self.assertTrue(any(r["name"] == ind.name for r in rows))

    def test_create_and_submit_goods_receipt_auto_creates_new_handling_unit(self):
        new_hu = frappe.generate_hash(length=10)
        self.assertFalse(frappe.db.exists("Handling Unit", new_hu))
        ind, hu = self._receive_and_putaway(9, hu_number=new_hu)
        self.assertTrue(frappe.db.exists("Handling Unit", new_hu))
        balance = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bulk_bin, "handling_unit": new_hu}, fields=["quantity"])
        self.assertEqual(balance[0].quantity, 9)

    def test_create_and_submit_goods_receipt_requires_hu_type_for_new_hu(self):
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 2, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            create_and_submit_goods_receipt(ind.name, [
                {"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 2, "stock_uom": self.uom, "handling_unit": frappe.generate_hash(length=10), "stock_type": "AVAILABLE"},
            ])

    def test_move_transfers_stock_and_can_be_confirmed_from_scanner_api(self):
        _ind, hu = self._receive_and_putaway(12)
        result = create_and_confirm_move(warehouse=self.warehouse, product=self.item, quantity=4, stock_uom=self.uom, stock_type="AVAILABLE",
            source_bin=self.bulk_bin, source_hu=hu, destination_bin=self.stage_bin, destination_hu=hu)
        self.assertEqual(result["status"], "Confirmed")
        bulk_qty = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bulk_bin, "handling_unit": hu}, fields=["quantity"])[0].quantity
        stage_qty = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.stage_bin, "handling_unit": hu}, fields=["quantity"])[0].quantity
        self.assertEqual(bulk_qty, 8)
        self.assertEqual(stage_qty, 4)

    def test_list_open_packing_orders_and_complete_via_scanner_api(self):
        _ind, hu = self._receive_and_putaway(10)
        hu2 = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "RFPARITY-PALLET", "warehouse": self.warehouse, "current_bin": self.bulk_bin, "status": "Open"})
        hu2.insert(ignore_permissions=True)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 1, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        order = frappe.get_doc({"doctype": "Packing Order", "outbound_delivery": obd.name, "work_center_bin": self.bulk_bin,
            "source_hus": [{"handling_unit": hu}], "destination_hus": [{"handling_unit": hu2.name}]})
        order.insert(ignore_permissions=True)

        open_orders = list_open_packing_orders()
        self.assertTrue(any(o["name"] == order.name for o in open_orders))
        self.assertEqual(open_orders[[o["name"] for o in open_orders].index(order.name)]["warehouse"], self.warehouse)

        complete_packing_order(order.name)
        dest_qty = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bulk_bin, "handling_unit": hu2.name}, fields=["quantity"])[0].quantity
        self.assertEqual(dest_qty, 10)

    def test_ship_flow_updates_issued_quantity_and_drops_off_ready_list(self):
        _ind, hu = self._receive_and_putaway(15)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 7, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        picked_hu = frappe.db.get_value("Warehouse Task", pick_tasks[0], "source_hu")
        confirm_task(pick_tasks[0], confirmed_quantity=7)

        ready = list_ready_to_ship()
        matching = [d for d in ready if d["name"] == obd.name]
        self.assertTrue(matching)
        self.assertEqual(matching[0]["items"][0]["remaining_quantity"], 7)
        self.assertEqual(matching[0]["items"][0]["suggested_handling_unit"], picked_hu)

        result = create_and_submit_goods_issue(obd.name, [
            {"outbound_delivery_item": obd.items[0].name, "item": self.item, "quantity": 7, "stock_uom": self.uom, "handling_unit": picked_hu, "stock_type": "AVAILABLE"},
        ])
        gi = frappe.get_doc("Goods Issue", result["goods_issue"])
        self.assertEqual(gi.status, "Posted")

        obd.reload()
        self.assertEqual(obd.items[0].issued_quantity, 7)
        self.assertEqual(obd.goods_issue_status, "Posted")
        self.assertEqual(obd.status, "Goods Issued")

        ready_after = list_ready_to_ship()
        self.assertFalse(any(d["name"] == obd.name for d in ready_after))

    def test_list_open_counts_and_inspections_are_warehouse_scoped_helpers(self):
        # Just exercises the endpoints end to end against real (possibly empty) data;
        # detailed count/inspection behavior is covered in test_phase4_inventory_management.py.
        counts = list_open_counts()
        inspections = list_open_inspections()
        self.assertIsInstance(counts, list)
        self.assertIsInstance(inspections, list)
