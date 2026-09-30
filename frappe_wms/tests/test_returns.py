import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, flt

from frappe_wms.api.inbound import create_putaway, create_inbound_delivery_from_purchase_order, create_and_submit_goods_receipt, create_return_inbound_delivery
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks, create_outbound_delivery_from_sales_order, create_return_outbound_delivery, create_and_submit_goods_issue
from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.inventory import complete_inspection
from frappe_wms.services.shipping import create_shipment, confirm_hu_loaded


class TestReturns(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("Handling Unit Type", "RETURNS-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "RETURNS-PALLET", "hu_type_name": "Returns Pallet"}).insert(ignore_permissions=True)

    def _new_scenario(self):
        # A fresh warehouse per test - same isolation reasoning as test_outbound_delivery_lifecycle.
        warehouse = f"WMS-TEST-RET-{frappe.generate_hash(length=6).upper()}"
        recv_bin, bulk_bin, stage_bin, door_bin = f"{warehouse}-RECV", f"{warehouse}-BULK", f"{warehouse}-STAGE", f"{warehouse}-DOOR"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": warehouse, "warehouse_name": warehouse, "company": self.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "DOOR", "storage_type_name": "DOOR", "storage_role": "Door", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((recv_bin, f"{warehouse}-GR"), (bulk_bin, f"{warehouse}-BULK"), (stage_bin, f"{warehouse}-GR"), (door_bin, f"{warehouse}-DOOR")):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", warehouse)
        wh.default_receiving_bin = recv_bin
        wh.default_shipping_bin = stage_bin
        wh.default_difference_bin = recv_bin
        wh.save(ignore_permissions=True)
        frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WMS Route", "route_code": f"{warehouse}-ROUTE", "route_name": f"{warehouse}-ROUTE",
            "origin_warehouse": warehouse, "default_staging_bin": stage_bin, "default_door": door_bin, "active": 1}).insert(ignore_permissions=True)
        return frappe._dict(warehouse=warehouse, recv_bin=recv_bin, bulk_bin=bulk_bin, stage_bin=stage_bin, door_bin=door_bin)

    def _make_hu(self, scenario, bin_name):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "RETURNS-PALLET", "warehouse": scenario.warehouse, "current_bin": bin_name, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def _full_outbound_flow(self, scenario, obd_name, hu):
        allocate_delivery(obd_name)
        tasks = create_pick_tasks(obd_name)
        for t in tasks:
            confirm_task(t)
        shipment = create_shipment(scenario.warehouse, [obd_name])
        confirm_hu_loaded(shipment, hu.name)  # auto-posts Goods Issue once fully loaded

    def test_customer_return_receives_into_quality_and_mirrors_to_sales_return(self):
        scenario = self._new_scenario()
        # Original sale: PO-free receipt straight to stock, then a real Sales-Order-linked
        # delivery so there's a genuine, submitted ERPNext Delivery Note to return against.
        hu = self._make_hu(scenario, scenario.recv_bin)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse, "supplier": self.supplier, "receiving_bin": scenario.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 10, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": scenario.warehouse, "receiving_bin": scenario.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 10, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        confirm_task(create_putaway(gr.name)["warehouse_tasks"][0], confirmed_quantity=10)

        so = frappe.get_doc({"doctype": "Sales Order", "customer": self.customer, "company": self.company, "transaction_date": nowdate(), "delivery_date": nowdate(),
            "items": [{"item_code": self.item, "qty": 10, "rate": 20, "delivery_date": nowdate(), "warehouse": frappe.db.get_value("WMS Warehouse", scenario.warehouse, "erpnext_warehouse")}]})
        so.insert(ignore_permissions=True)
        so.submit()
        obd_name = create_outbound_delivery_from_sales_order(so.name, scenario.warehouse)
        obd = frappe.get_doc("Outbound Delivery", obd_name)
        obd.submit()
        self._full_outbound_flow(scenario, obd.name, hu)

        gi = frappe.get_all("Goods Issue", filters={"outbound_delivery": obd.name}, pluck="name")[0]
        original_dn = frappe.db.get_value("Goods Issue", gi, "erpnext_delivery_note")
        self.assertTrue(original_dn)

        # Now the customer returns it.
        ind_name = create_return_inbound_delivery(original_dn, scenario.warehouse)
        ind = frappe.get_doc("Inbound Delivery", ind_name)
        self.assertEqual(ind.items[0].expected_stock_type, "QUALITY")
        return_hu = self._make_hu(scenario, scenario.recv_bin)
        result = create_and_submit_goods_receipt(ind.name, frappe.as_json([
            {"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 4, "stock_uom": self.uom, "handling_unit": return_hu.name, "stock_type": "QUALITY"},
        ]))
        gr2 = frappe.get_doc("Goods Receipt", result["goods_receipt"])
        self.assertTrue(gr2.erpnext_delivery_note)
        return_dn = frappe.get_doc("Delivery Note", gr2.erpnext_delivery_note)
        self.assertEqual(return_dn.is_return, 1)
        self.assertEqual(return_dn.return_against, original_dn)
        self.assertEqual(flt(return_dn.items[0].qty), -4)
        # ERPNext itself has to know 4 came back (it didn't, when the return was submitted from the
        # mapper-built object - see erpnext_sync._submit_return)
        self.assertEqual(flt(frappe.db.get_value("Delivery Note Item", return_dn.items[0].dn_detail, "returned_qty")), 4)
        # 4 received, 6 still expected on this open return - nothing left to raise a second one for
        with self.assertRaises(frappe.ValidationError):
            create_return_inbound_delivery(original_dn, scenario.warehouse)

        # Was: only a matching Inspection Rule ever created a WMS Quality Inspection - a return
        # (no rule needed) used to sit in QUALITY with no inspection to act on at all.
        inspection = frappe.get_all("WMS Quality Inspection", filters={"goods_receipt": gr2.name}, pluck="name")[0]
        complete_inspection(inspection, passed_quantity=3, failed_quantity=1)
        available_qty = flt(frappe.db.get_value("WMS Stock Balance", {
            "warehouse": scenario.warehouse, "product": self.item, "handling_unit": return_hu.name, "stock_type": "AVAILABLE",
        }, "quantity"))
        self.assertEqual(available_qty, 3)

    def test_vendor_return_mirrors_to_return_purchase_receipt(self):
        scenario = self._new_scenario()
        po = frappe.get_doc({"doctype": "Purchase Order", "supplier": self.supplier, "company": self.company, "transaction_date": nowdate(), "schedule_date": nowdate(),
            "items": [{"item_code": self.item, "qty": 10, "rate": 8, "schedule_date": nowdate()}]})
        po.insert(ignore_permissions=True)
        po.submit()
        ind_name = create_inbound_delivery_from_purchase_order(po.name, scenario.warehouse)
        ind = frappe.get_doc("Inbound Delivery", ind_name)
        hu = self._make_hu(scenario, scenario.recv_bin)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": scenario.warehouse, "receiving_bin": scenario.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 10, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        confirm_task(create_putaway(gr.name)["warehouse_tasks"][0], confirmed_quantity=10)
        self.assertTrue(gr.erpnext_purchase_receipt)

        obd_name = create_return_outbound_delivery(gr.erpnext_purchase_receipt, scenario.warehouse, stock_type="AVAILABLE")
        obd = frappe.get_doc("Outbound Delivery", obd_name)
        self.assertEqual(obd.items[0].required_stock_type, "AVAILABLE")
        obd.submit()
        self._full_outbound_flow(scenario, obd.name, hu)

        gi = frappe.get_all("Goods Issue", filters={"outbound_delivery": obd.name}, pluck="name")[0]
        gi_doc = frappe.get_doc("Goods Issue", gi)
        self.assertTrue(gi_doc.erpnext_purchase_receipt)
        ret_pr = frappe.get_doc("Purchase Receipt", gi_doc.erpnext_purchase_receipt)
        self.assertEqual(ret_pr.is_return, 1)
        self.assertEqual(ret_pr.return_against, gr.erpnext_purchase_receipt)
        self.assertEqual(flt(ret_pr.items[0].qty), -10)

    def test_over_confirmation_gain_is_mirrored_to_erpnext(self):
        # An over-confirmed putaway posts the extra units into the difference bin as an inventory
        # gain - that has to reach ERPNext too, or the two ledgers drift apart for good.
        scenario = self._new_scenario()
        erpnext_warehouse = frappe.db.get_value("WMS Warehouse", scenario.warehouse, "erpnext_warehouse")
        diff_bin = frappe.db.get_value("WMS Warehouse", scenario.warehouse, "default_difference_bin")
        if not diff_bin:
            frappe.db.set_value("WMS Warehouse", scenario.warehouse, "default_difference_bin", scenario.recv_bin)
        hu = self._make_hu(scenario, scenario.recv_bin)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse, "supplier": self.supplier, "receiving_bin": scenario.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 10, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        result = create_and_submit_goods_receipt(ind.name, frappe.as_json([
            {"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 10, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]))
        before = flt(frappe.db.sql("select sum(actual_qty) from `tabStock Ledger Entry` where item_code=%s and warehouse=%s and is_cancelled=0", (self.item, erpnext_warehouse))[0][0])
        outcome = confirm_task(result["warehouse_tasks"][0], confirmed_quantity=12)
        difference = frappe.get_doc("WMS Task Difference", outcome["difference"])
        self.assertTrue(difference.erpnext_stock_entry)
        after = flt(frappe.db.sql("select sum(actual_qty) from `tabStock Ledger Entry` where item_code=%s and warehouse=%s and is_cancelled=0", (self.item, erpnext_warehouse))[0][0])
        self.assertEqual(after - before, 2)

