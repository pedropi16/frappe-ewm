import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task


class TestPutawayAndPickFlow(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-WH"
        cls.recv_bin = "WMS-TEST-WH-GR-RECV"
        cls.stage_bin = "WMS-TEST-WH-GR-STAGE"
        cls.bulk_bin = "WMS-TEST-WH-BULK-A1"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "Receiving", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "Bulk", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, storage_type in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.stage_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": storage_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        if not wh.default_receiving_bin:
            wh.default_receiving_bin = cls.recv_bin
            wh.default_shipping_bin = cls.stage_bin
            wh.default_difference_bin = cls.recv_bin
            wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-PALLET", "hu_type_name": "Test Pallet"}).insert(ignore_permissions=True)

    def _make_hu(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu.name

    def test_putaway_then_pick_moves_stock_and_releases_allocation(self):
        hu = self._make_hu()

        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 20, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)

        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 20, "stock_uom": self.uom, "handling_unit": hu, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()

        result = create_putaway(gr.name)
        self.assertEqual(len(result["warehouse_tasks"]), 1)
        confirm_task(result["warehouse_tasks"][0], confirmed_quantity=20)

        bulk_balance = frappe.get_all("WMS Stock Balance", filters={"product": self.item, "storage_bin": self.bulk_bin, "handling_unit": hu}, fields=["quantity", "available_quantity"])
        self.assertEqual(len(bulk_balance), 1)
        self.assertEqual(bulk_balance[0].quantity, 20)
        self.assertEqual(bulk_balance[0].available_quantity, 20)

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()

        allocations = allocate_delivery(obd.name)
        self.assertEqual(len(allocations), 1)

        bulk_balance_after_allocation = frappe.get_all("WMS Stock Balance", filters={"product": self.item, "storage_bin": self.bulk_bin, "handling_unit": hu}, fields=["allocated_quantity", "available_quantity"])[0]
        self.assertEqual(bulk_balance_after_allocation.allocated_quantity, 5)
        self.assertEqual(bulk_balance_after_allocation.available_quantity, 15)

        pick_tasks = create_pick_tasks(obd.name)
        self.assertEqual(len(pick_tasks), 1)
        confirm_task(pick_tasks[0], confirmed_quantity=5)

        bulk_balance_after_pick = frappe.get_all("WMS Stock Balance", filters={"product": self.item, "storage_bin": self.bulk_bin, "handling_unit": hu}, fields=["quantity", "allocated_quantity", "available_quantity"])[0]
        self.assertEqual(bulk_balance_after_pick.quantity, 15)
        self.assertEqual(bulk_balance_after_pick.allocated_quantity, 0, "picking must release the reservation on the source balance")
        self.assertEqual(bulk_balance_after_pick.available_quantity, 15)

        stage_balance = frappe.get_all("WMS Stock Balance", filters={"product": self.item, "storage_bin": self.stage_bin, "handling_unit": hu}, fields=["quantity"])[0]
        self.assertEqual(stage_balance.quantity, 5)

        allocation = frappe.get_doc("Stock Allocation", allocations[0])
        self.assertEqual(allocation.status, "Picked")
        self.assertEqual(allocation.picked_quantity, 5)

        obd.reload()
        self.assertEqual(obd.picking_status, "Picked")
        self.assertEqual(obd.items[0].picked_quantity, 5)
