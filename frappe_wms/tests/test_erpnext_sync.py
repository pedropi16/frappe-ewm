import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task
from frappe_wms.tasks import verify_erpnext_stock_reconciliation


class TestErpnextSync(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "ERPSYNC-TEST-WH"
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
        if not frappe.db.exists("Handling Unit Type", "ERPSYNC-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "ERPSYNC-PALLET", "hu_type_name": "ERPSync Pallet"}).insert(ignore_permissions=True)

    def _erpnext_qty(self):
        return frappe.db.get_value("Bin", {"warehouse": self.wh.erpnext_warehouse, "item_code": self.item}, "actual_qty") or 0

    def test_wms_warehouse_auto_links_erpnext_warehouse(self):
        self.assertTrue(self.wh.erpnext_warehouse)
        self.assertTrue(frappe.db.exists("Warehouse", self.wh.erpnext_warehouse))

    def test_goods_receipt_and_issue_sync_to_erpnext_and_reverse_on_cancel(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "ERPSYNC-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)

        qty_before = self._erpnext_qty()

        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 7, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)

        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 7, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()

        self.assertTrue(gr.erpnext_stock_entry)
        se = frappe.get_doc("Stock Entry", gr.erpnext_stock_entry)
        self.assertEqual(se.stock_entry_type, "Material Receipt")
        self.assertEqual(se.docstatus, 1)
        self.assertEqual(self._erpnext_qty(), qty_before + 7)

        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=7)

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 3, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=3)

        # No manual HU status override here: picking must auto-stage the HU on its own.
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "status"), "Staged")

        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": self.stage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": self.item, "quantity": 3, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gi.insert(ignore_permissions=True)
        gi.submit()

        self.assertTrue(gi.erpnext_stock_entry)
        se2 = frappe.get_doc("Stock Entry", gi.erpnext_stock_entry)
        self.assertEqual(se2.stock_entry_type, "Material Issue")
        self.assertEqual(self._erpnext_qty(), qty_before + 4)

        gi.cancel()
        se2.reload()
        self.assertEqual(se2.docstatus, 2)
        self.assertEqual(self._erpnext_qty(), qty_before + 7)
        gi.reload()
        self.assertEqual(gi.status, "Reversed")
        self.assertEqual(gi.reversed, 1)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "status"), "Staged", "reversing the issue should put the HU back into Staged status")

        # Reconciliation should find no drift after a clean sequence of postings.
        verify_erpnext_stock_reconciliation()
