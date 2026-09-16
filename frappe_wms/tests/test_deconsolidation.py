import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.deconsolidation import create_deconsolidation_tasks
from frappe_wms.api.scanner import confirm_task


class TestDeconsolidation(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-DECON-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bin_a = f"{cls.warehouse}-A"
        cls.bin_b = f"{cls.warehouse}-B"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-A"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "A", "storage_type_name": "Zone A", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bin_a, f"{cls.warehouse}-A"), (cls.bin_b, f"{cls.warehouse}-A")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-DECON-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-DECON-PALLET", "hu_type_name": "Test Decon Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Warehouse Queue", "WMS-TEST-DECON-QUEUE"):
            frappe.get_doc({"doctype": "Warehouse Queue", "queue_code": "WMS-TEST-DECON-QUEUE", "queue_name": "Test Decon Queue", "warehouse": cls.warehouse, "activity": "Deconsolidation", "active": 1}).insert(ignore_permissions=True)

    def _receive_hu(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-DECON-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return hu

    def test_split_across_two_bins_creates_gated_tasks(self):
        hu = self._receive_hu(20)
        lines = [
            {"product": self.item, "stock_type": "AVAILABLE", "quantity": 12, "destination_bin": self.bin_a},
            {"product": self.item, "stock_type": "AVAILABLE", "quantity": 8, "destination_bin": self.bin_b},
        ]
        created = create_deconsolidation_tasks(hu.name, lines)
        self.assertEqual(len(created), 2)

        first, second = (frappe.get_doc("Warehouse Task", name) for name in created)
        self.assertEqual(first.task_type, "Deconsolidation")
        self.assertNotEqual(first.status, "On Hold")
        self.assertEqual(second.status, "On Hold")

        confirm_task(first.name, confirmed_quantity=12)
        second.reload()
        self.assertIn(second.status, ("Open", "Assigned"))
        result = confirm_task(second.name, confirmed_quantity=8)
        self.assertEqual(result["status"], "Confirmed")

        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.bin_a, "product": self.item}, "quantity"), 12)
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.bin_b, "product": self.item}, "quantity"), 8)

    def test_line_exceeding_available_quantity_is_rejected(self):
        hu = self._receive_hu(5)
        lines = [{"product": self.item, "stock_type": "AVAILABLE", "quantity": 50, "destination_bin": self.bin_a}]
        with self.assertRaises(frappe.ValidationError):
            create_deconsolidation_tasks(hu.name, lines)
