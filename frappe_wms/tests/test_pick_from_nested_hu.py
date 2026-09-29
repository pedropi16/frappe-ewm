import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.handling_unit import nest_handling_unit


class TestPickFromNestedHu(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "NESTPICK-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bulk_bin = f"{cls.warehouse}-BULK"
        cls.ship_bin = f"{cls.warehouse}-SHIP"
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
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
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-DOOR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "DOOR", "storage_type_name": "DOOR", "storage_role": "Door", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bulk_bin, f"{cls.warehouse}-BULK"), (cls.ship_bin, f"{cls.warehouse}-DOOR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_shipping_bin = cls.ship_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "NESTPICK-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "NESTPICK-PALLET", "hu_type_name": "Nestpick Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "NESTPICK-TOTE"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "NESTPICK-TOTE", "hu_type_name": "Nestpick Tote"}).insert(ignore_permissions=True)

    def _make_hu(self, bin_name, hu_type="NESTPICK-PALLET"):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": hu_type, "warehouse": self.warehouse, "current_bin": bin_name, "status": "Open"})
        hu.insert(ignore_permissions=True)
        return hu

    def test_picking_stock_out_of_a_repacked_hu_unnests_it_instead_of_throwing(self):
        # Reproduces the production bug directly: Diego repacks a receipt's HU (SSCC...) into a
        # new tote (nest_handling_unit), then Bruno picks stock straight out of it - the picked
        # HU (still a child of that tote) gets relocated to the pick's destination on its own,
        # while the tote it was nested in never moves. Before the fix, _move_hu_and_descendants
        # tried to save the child at its new bin while its parent stayed at the old one, and
        # validate_hu's own "same warehouse and bin" check correctly rejected that.
        child_hu = self._make_hu(self.recv_bin)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 20, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 20, "stock_uom": self.uom, "handling_unit": child_hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=20)
        child_hu.reload()
        self.assertEqual(child_hu.current_bin, self.bulk_bin)

        parent_hu = self._make_hu(self.bulk_bin, hu_type="NESTPICK-TOTE")
        nest_handling_unit(child_hu.name, parent_hu.name)
        child_hu.reload()
        self.assertEqual(child_hu.parent_hu, parent_hu.name)

        pick_task = frappe.get_doc({
            "doctype": "Warehouse Task", "task_type": "Pick", "warehouse": self.warehouse,
            "product": self.item, "planned_quantity": 20, "stock_uom": self.uom,
            "source_bin": self.bulk_bin, "source_hu": child_hu.name, "destination_bin": self.ship_bin,
            "stock_type_from": "AVAILABLE", "stock_type_to": "AVAILABLE",
            "movement_type": "601", "priority": "Normal", "status": "Open",
        })
        pick_task.insert(ignore_permissions=True)

        confirm_task(pick_task.name, confirmed_quantity=20)  # must not raise

        child_hu.reload()
        parent_hu.reload()
        self.assertEqual(child_hu.current_bin, self.ship_bin, "the picked HU must actually move to the pick's destination")
        self.assertFalse(child_hu.parent_hu, "picking it out must detach it from the tote it was repacked into")
        self.assertEqual(parent_hu.current_bin, self.bulk_bin, "the tote itself was never part of this pick and must stay put")
