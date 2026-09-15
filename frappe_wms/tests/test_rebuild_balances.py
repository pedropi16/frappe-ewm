import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task
from frappe_wms.services.stock import rebuild_balances


class TestRebuildBalances(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "REBUILD-TEST-WH"
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
        if not frappe.db.exists("Handling Unit Type", "REBUILD-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "REBUILD-PALLET", "hu_type_name": "Rebuild Pallet"}).insert(ignore_permissions=True)

    def test_rebuild_recomputes_corrupted_balance_from_ledger(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "REBUILD-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 12, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 12, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=12)

        balance_name = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bulk_bin, "handling_unit": hu.name}, pluck="name")[0]
        frappe.db.set_value("WMS Stock Balance", balance_name, {"quantity": 999, "available_quantity": 999})
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", balance_name, "quantity"), 999)

        touched = rebuild_balances(warehouse=self.warehouse, product=self.item)
        self.assertIn(balance_name, touched)

        doc = frappe.get_doc("WMS Stock Balance", balance_name)
        self.assertEqual(doc.quantity, 12)
        self.assertEqual(doc.available_quantity, 12)

    def test_rebuild_preserves_allocated_quantity(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "REBUILD-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 5, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 5, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=5)

        balance_name = frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bulk_bin, "handling_unit": hu.name}, pluck="name")[0]
        frappe.db.set_value("WMS Stock Balance", balance_name, "allocated_quantity", 2)

        rebuild_balances(warehouse=self.warehouse, product=self.item)

        doc = frappe.get_doc("WMS Stock Balance", balance_name)
        self.assertEqual(doc.quantity, 5)
        self.assertEqual(doc.allocated_quantity, 2, "rebuild must not touch reservations, only the ledger-derived quantity")
        self.assertEqual(doc.available_quantity, 3)
