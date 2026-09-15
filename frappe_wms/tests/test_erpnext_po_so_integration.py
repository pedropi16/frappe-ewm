import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, add_days

from frappe_wms.api.inbound import create_putaway, create_inbound_delivery_from_purchase_order
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks, create_outbound_delivery_from_sales_order
from frappe_wms.api.scanner import confirm_task


class TestErpnextPoSoIntegration(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PHASE5C-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.company = company

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
        if not frappe.db.exists("Handling Unit Type", "PHASE5C-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PHASE5C-PALLET", "hu_type_name": "Phase5c Pallet"}).insert(ignore_permissions=True)

    def _make_hu(self, bin_name):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PHASE5C-PALLET", "warehouse": self.warehouse, "current_bin": bin_name, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def test_purchase_order_receipt_posts_purchase_receipt_and_updates_po(self):
        po = frappe.get_doc({"doctype": "Purchase Order", "supplier": self.supplier, "company": self.company, "transaction_date": nowdate(), "schedule_date": nowdate(),
            "items": [{"item_code": self.item, "qty": 10, "rate": 10, "schedule_date": nowdate()}]})
        po.insert(ignore_permissions=True)
        po.submit()

        ind_name = create_inbound_delivery_from_purchase_order(po.name, self.warehouse)
        ind = frappe.get_doc("Inbound Delivery", ind_name)
        self.assertEqual(ind.items[0].purchase_order, po.name)
        self.assertEqual(ind.items[0].expected_quantity, 10)

        hu = self._make_hu(self.recv_bin)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 10, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()

        self.assertTrue(gr.erpnext_purchase_receipt)
        self.assertFalse(gr.erpnext_stock_entry)
        pr = frappe.get_doc("Purchase Receipt", gr.erpnext_purchase_receipt)
        self.assertEqual(pr.docstatus, 1)
        self.assertEqual(pr.items[0].qty, 10)

        po.reload()
        self.assertEqual(po.per_received, 100)

        gr.cancel()
        pr.reload()
        po.reload()
        self.assertEqual(pr.docstatus, 2)
        self.assertEqual(po.per_received, 0)

    def test_mixed_po_and_standalone_lines_are_rejected(self):
        po = frappe.get_doc({"doctype": "Purchase Order", "supplier": self.supplier, "company": self.company, "transaction_date": nowdate(), "schedule_date": nowdate(),
            "items": [{"item_code": self.item, "qty": 5, "rate": 10, "schedule_date": nowdate()}]})
        po.insert(ignore_permissions=True)
        po.submit()
        ind_name = create_inbound_delivery_from_purchase_order(po.name, self.warehouse)
        ind = frappe.get_doc("Inbound Delivery", ind_name)

        ind2 = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 3, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind2.insert(ignore_permissions=True)

        hu = self._make_hu(self.recv_bin)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [
                {"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 5, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"},
                {"inbound_delivery_item": ind2.items[0].name, "item": self.item, "quantity": 3, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"},
            ]})
        gr.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            gr.submit()

    def test_sales_order_issue_posts_delivery_note_and_updates_so(self):
        hu = self._make_hu(self.recv_bin)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 15, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 15, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=15)

        so = frappe.get_doc({"doctype": "Sales Order", "customer": self.customer, "company": self.company, "transaction_date": nowdate(), "delivery_date": add_days(nowdate(), 2),
            "items": [{"item_code": self.item, "qty": 4, "rate": 20, "delivery_date": add_days(nowdate(), 2)}]})
        so.insert(ignore_permissions=True)
        so.submit()

        obd_name = create_outbound_delivery_from_sales_order(so.name, self.warehouse)
        obd = frappe.get_doc("Outbound Delivery", obd_name)
        self.assertEqual(obd.items[0].sales_order, so.name)
        self.assertEqual(obd.items[0].requested_quantity, 4)

        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        picked_hu = frappe.db.get_value("Warehouse Task", pick_tasks[0], "source_hu")
        confirm_task(pick_tasks[0], confirmed_quantity=4)

        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": self.stage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": self.item, "quantity": 4, "stock_uom": self.uom, "handling_unit": picked_hu, "stock_type": "AVAILABLE"}]})
        gi.insert(ignore_permissions=True)
        gi.submit()

        self.assertTrue(gi.erpnext_delivery_note)
        self.assertFalse(gi.erpnext_stock_entry)
        dn = frappe.get_doc("Delivery Note", gi.erpnext_delivery_note)
        self.assertEqual(dn.docstatus, 1)
        self.assertEqual(dn.items[0].qty, 4)

        so.reload()
        self.assertEqual(so.per_delivered, 100)

        gi.cancel()
        dn.reload()
        so.reload()
        self.assertEqual(dn.docstatus, 2)
        self.assertEqual(so.per_delivered, 0)

    def test_create_inbound_delivery_requires_submitted_po(self):
        po = frappe.get_doc({"doctype": "Purchase Order", "supplier": self.supplier, "company": self.company, "transaction_date": nowdate(), "schedule_date": nowdate(),
            "items": [{"item_code": self.item, "qty": 2, "rate": 10, "schedule_date": nowdate()}]})
        po.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            create_inbound_delivery_from_purchase_order(po.name, self.warehouse)
