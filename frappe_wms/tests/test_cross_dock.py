import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.services.receipt import create_putaway_requests
from frappe_wms.services.task import create_tasks_for_request
from frappe_wms.api.scanner import confirm_task


class TestCrossDock(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "XDOCK-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        cls.uom = "Nos"
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-BULK"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-GR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "XDOCK-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "XDOCK-PALLET", "hu_type_name": "XDock Pallet"}).insert(ignore_permissions=True)

    def _make_item(self, item_code):
        # Per-test item (not the shared fixture item) - open outbound demand from an earlier
        # test method in this class must not leak into a later one via find_cross_dock_demand,
        # since IntegrationTestCase methods share state within a class.
        if not frappe.db.exists("Item", item_code):
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": self.item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        return item_code

    def _make_hu(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "XDOCK-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def _submit_gr(self, hu, item, qty):
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return gr

    def _make_delivery(self, item, qty):
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        return obd

    def test_receipt_matching_open_demand_creates_cross_dock_request_instead_of_putaway(self):
        item = self._make_item("TEST-XDOCK-ITEM-1")
        obd = self._make_delivery(item, 6)
        hu = self._make_hu()
        gr = self._submit_gr(hu, item, 6)

        request_names = create_putaway_requests(gr.name)
        self.assertEqual(len(request_names), 1)
        request = frappe.get_doc("Warehouse Request", request_names[0])
        self.assertEqual(request.request_type, "Cross Dock")
        self.assertEqual(request.destination_bin, self.stage_bin)
        self.assertEqual(request.reference_doctype, "Outbound Delivery")
        self.assertEqual(request.reference_name, obd.name)
        self.assertEqual(request.requested_quantity, 6)

    def test_partial_match_splits_into_cross_dock_and_putaway_requests(self):
        item = self._make_item("TEST-XDOCK-ITEM-2")
        self._make_delivery(item, 4)
        hu = self._make_hu()
        gr = self._submit_gr(hu, item, 10)

        request_names = create_putaway_requests(gr.name)
        self.assertEqual(len(request_names), 2)
        requests = {r.request_type: r for r in [frappe.get_doc("Warehouse Request", n) for n in request_names]}
        self.assertIn("Cross Dock", requests)
        self.assertIn("Putaway", requests)
        self.assertEqual(requests["Cross Dock"].requested_quantity, 4)
        self.assertEqual(requests["Putaway"].requested_quantity, 6)

    def test_confirming_cross_dock_task_fulfills_delivery_without_stock_allocation(self):
        item = self._make_item("TEST-XDOCK-ITEM-3")
        obd = self._make_delivery(item, 5)
        hu = self._make_hu()
        gr = self._submit_gr(hu, item, 5)
        request_names = create_putaway_requests(gr.name)
        task_name = create_tasks_for_request(request_names[0])
        confirm_task(task_name, confirmed_quantity=5)

        obd.reload()
        self.assertEqual(obd.items[0].picked_quantity, 5)
        self.assertEqual(obd.items[0].allocated_quantity, 5)
        self.assertEqual(obd.picking_status, "Picked")
        self.assertEqual(frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name}), [])

    def test_cross_dock_reserves_the_delivery_line_straight_away(self):
        # The matched demand is claimed when the Cross Dock request is raised, so neither a release
        # nor a second receipt can fulfil the same line again before the task is confirmed.
        from frappe_wms.services.allocation import allocate_delivery
        item = self._make_item("TEST-XDOCK-ITEM-6")
        obd = self._make_delivery(item, 5)
        gr = self._submit_gr(self._make_hu(), item, 5)
        self.assertEqual([frappe.db.get_value("Warehouse Request", n, "request_type") for n in create_putaway_requests(gr.name)], ["Cross Dock"])
        obd.reload()
        self.assertEqual(obd.items[0].allocated_quantity, 5)
        self.assertEqual(obd.allocation_status, "Fully Allocated")
        allocate_delivery(obd.name)
        self.assertEqual(frappe.get_all("Stock Allocation", filters={"outbound_delivery": obd.name}), [])
        gr2 = self._submit_gr(self._make_hu(), item, 5)
        self.assertEqual([frappe.db.get_value("Warehouse Request", n, "request_type") for n in create_putaway_requests(gr2.name)], ["Putaway"])

    def test_cross_docked_delivery_can_be_shipped_and_issued(self):
        # A cross-docked delivery has no Stock Allocation or Pick task, which is all shipping and
        # Goods Issue used to look at - it had "no staged Handling Unit" and could never leave.
        from frappe_wms.services.shipping import create_shipment, confirm_hu_loaded
        door_type, door_bin, route = f"{self.warehouse}-DOOR", f"{self.warehouse}-DOOR-1", f"{self.warehouse}-ROUTE"
        if not frappe.db.exists("Storage Type", door_type):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": self.warehouse, "storage_type_code": "DOOR", "storage_type_name": "DOOR", "storage_role": "Door", "capacity_check_method": "None", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", door_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": door_bin, "warehouse": self.warehouse, "storage_type": door_type, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Route", route):
            frappe.get_doc({"doctype": "WMS Route", "route_code": route, "route_name": route, "origin_warehouse": self.warehouse,
                "default_staging_bin": self.stage_bin, "default_door": door_bin, "active": 1}).insert(ignore_permissions=True)
        item = self._make_item("TEST-XDOCK-ITEM-5")
        obd = self._make_delivery(item, 5)
        hu = self._make_hu()
        gr = self._submit_gr(hu, item, 5)
        task_name = create_tasks_for_request(create_putaway_requests(gr.name)[0])
        confirm_task(task_name, confirmed_quantity=5)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "status"), "Staged")

        shipment = create_shipment(self.warehouse, [obd.name], route=route)
        self.assertEqual([r.handling_unit for r in frappe.get_doc("WMS Shipment", shipment).handling_units], [hu.name])
        confirm_hu_loaded(shipment, hu.name)
        obd.reload()
        self.assertEqual(obd.goods_issue_status, "Posted")
        self.assertEqual(obd.items[0].issued_quantity, 5)

    def test_receipt_with_no_open_demand_is_unaffected(self):
        item = self._make_item("TEST-XDOCK-ITEM-4")
        hu = self._make_hu()
        gr = self._submit_gr(hu, item, 8)
        request_names = create_putaway_requests(gr.name)
        self.assertEqual(len(request_names), 1)
        request = frappe.get_doc("Warehouse Request", request_names[0])
        self.assertEqual(request.request_type, "Putaway")
        self.assertEqual(request.requested_quantity, 8)

    def test_cancelled_delivery_sends_open_cross_dock_stock_to_putaway(self):
        item = self._make_item("TEST-XDOCK-ITEM-CANCEL")
        obd = self._make_delivery(item, 5)
        gr = self._submit_gr(self._make_hu(), item, 5)
        [cd] = create_putaway_requests(gr.name)
        self.assertEqual(frappe.db.get_value("Warehouse Request", cd, "request_type"), "Cross Dock")
        from frappe_wms.services.task import create_tasks_for_request
        create_tasks_for_request(cd)
        obd.reload()
        obd.cancel()
        self.assertEqual(frappe.db.get_value("Warehouse Request", cd, "status"), "Cancelled")
        self.assertFalse(frappe.db.exists("Warehouse Task", {"warehouse_request": cd, "docstatus": 0}), "open cross-dock tasks are cancelled")
        putaway = frappe.get_all("Warehouse Request", filters={"request_type": "Putaway", "reference_name": cd},
                                 fields=["name", "requested_quantity", "source_bin"])
        self.assertEqual([(p.requested_quantity, p.source_bin) for p in putaway], [(5, self.recv_bin)])
        self.assertTrue(frappe.db.exists("Warehouse Task", {"warehouse_request": putaway[0].name, "task_type": "Putaway"}))
