import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.movement import close_movement


class TestCloseMovement(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMS-TEST-CLOSE-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.final_bin = f"{cls.warehouse}-FINAL"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        cls.item2 = "TEST-CLOSE-ITEM-2"
        if not frappe.db.exists("Item", cls.item2):
            frappe.get_doc({"doctype": "Item", "item_code": cls.item2, "item_name": cls.item2, "item_group": item_group, "stock_uom": cls.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        cls.uom2 = cls.uom
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in ((f"{cls.warehouse}-GR", "Receiving"), (f"{cls.warehouse}-BULK", "Storage"), (f"{cls.warehouse}-STAGE", "Staging"), (f"{cls.warehouse}-FINAL", "Storage")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.stage_bin, f"{cls.warehouse}-STAGE"), (cls.final_bin, f"{cls.warehouse}-FINAL")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)

        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Internal Move", "source_storage_type": f"{cls.warehouse}-BULK"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1, "priority": 1, "source_storage_type": f"{cls.warehouse}-BULK", "destination_storage_type": f"{cls.warehouse}-STAGE", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Internal Move", "source_storage_type": f"{cls.warehouse}-STAGE"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Internal Move", "active": 1, "priority": 1, "source_storage_type": f"{cls.warehouse}-STAGE", "destination_storage_type": f"{cls.warehouse}-FINAL", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-CLOSE-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-CLOSE-PALLET", "hu_type_name": "Test Close Pallet"}).insert(ignore_permissions=True)

    def _receive_and_putaway(self, qty, item=None):
        item = item or self.item
        uom = frappe.db.get_value("Item", item, "stock_uom")
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-CLOSE-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        for task_name in putaway["warehouse_tasks"]:
            confirm_task(task_name, confirmed_quantity=qty)
        return hu

    def test_close_movement_chains_through_bin_determination_rules(self):
        hu = self._receive_and_putaway(15)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), self.bulk_bin)

        first_hop = close_movement(hu.name)
        self.assertEqual(first_hop["destination_bin"], self.stage_bin)
        self.assertEqual(len(first_hop["tasks"]), 1)
        confirm_task(first_hop["tasks"][0], confirmed_quantity=15)
        hu.reload()
        self.assertEqual(hu.current_bin, self.stage_bin)

        second_hop = close_movement(hu.name)
        self.assertEqual(second_hop["destination_bin"], self.final_bin)
        self.assertNotEqual(second_hop["destination_bin"], first_hop["destination_bin"])

    def test_close_movement_on_multi_product_hu_creates_ungated_sibling_tasks(self):
        if self.item2 == self.item:
            self.skipTest("need at least two distinct stock items in this site's fixture data")
        hu = self._receive_and_putaway(6)
        # Post a second product directly onto the same HU/bin (a single unbalanced "gain" entry,
        # the same shape post_count() uses for variances) so the HU carries two distinct lines.
        from frappe_wms.services.stock import post_entries
        post_entries(
            [{"warehouse": self.warehouse, "product": self.item2, "handling_unit": hu.name, "storage_bin": self.bulk_bin,
              "stock_type": "AVAILABLE", "stock_uom": self.uom2, "quantity": 4, "movement_type": "701"}],
            "Handling Unit", hu.name, f"test-close-second-product:{hu.name}",
        )
        result = close_movement(hu.name)
        self.assertEqual(len(result["tasks"]), 2)
        statuses = {frappe.db.get_value("Warehouse Task", t, "status") for t in result["tasks"]}
        self.assertNotIn("On Hold", statuses)

    def test_close_movement_requires_stock_on_the_hu(self):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-CLOSE-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            close_movement(hu.name)
