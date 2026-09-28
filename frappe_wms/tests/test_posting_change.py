import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.posting_change import post_posting_change, cancel_posting_change


class TestPostingChange(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "PSC-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.bin1 = f"{cls.warehouse}-BIN1"
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.wh = frappe.get_doc("WMS Warehouse", cls.warehouse)
        for code, role in ((f"{cls.warehouse}-GR", "Receiving"), (f"{cls.warehouse}-ST", "Storage")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "hu_managed": 0, "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.recv_bin, f"{cls.warehouse}-GR"), (cls.bin1, f"{cls.warehouse}-ST")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not cls.wh.default_receiving_bin:
            cls.wh.default_receiving_bin = cls.recv_bin
            cls.wh.default_difference_bin = cls.recv_bin
            cls.wh.save(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": cls.warehouse, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": cls.warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{cls.warehouse}-ST", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Item", "PSC-TEST-ITEM"):
            frappe.get_doc({"doctype": "Item", "item_code": "PSC-TEST-ITEM", "item_name": "PSC Test Item", "item_group": "All Item Groups", "stock_uom": "Nos", "is_stock_item": 1}).insert(ignore_permissions=True)
        cls.item = "PSC-TEST-ITEM"
        if not frappe.db.exists("Handling Unit Type", "PSC-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PSC-PALLET", "hu_type_name": "PSC Pallet"}).insert(ignore_permissions=True)

    def test_post_and_cancel_posting_change(self):
        # Seeded through a real, ERPNext-mirrored Goods Receipt - not raw post_entries - since
        # the posting change mirrors as a same-warehouse Stock Entry that DEDUCTS from ERPNext's
        # own on-hand qty; WMS-only stock with nothing ever mirrored to ERPNext has none to deduct.
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PSC-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 10, "stock_uom": "Nos", "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 10, "stock_uom": "Nos", "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        confirm_task(create_putaway(gr.name)["warehouse_tasks"][0], confirmed_quantity=10)
        bin1 = frappe.db.get_value("Handling Unit", hu.name, "current_bin")

        change = frappe.get_doc({"doctype": "WMS Posting Change", "warehouse": self.warehouse, "product": self.item,
            "storage_bin": bin1, "handling_unit": hu.name, "from_stock_type": "AVAILABLE", "to_stock_type": "WAREHOUSE_BLOCKED",
            "quantity": 4, "stock_uom": "Nos", "reason": "Recall hold"})
        change.insert(ignore_permissions=True)

        result = post_posting_change(change.name)
        self.assertEqual(result["status"], "Posted")

        available = flt(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.warehouse, "product": self.item, "storage_bin": bin1, "handling_unit": hu.name, "stock_type": "AVAILABLE"}, "quantity"))
        blocked = flt(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.warehouse, "product": self.item, "storage_bin": bin1, "handling_unit": hu.name, "stock_type": "WAREHOUSE_BLOCKED"}, "quantity"))
        self.assertEqual(available, 6)
        self.assertEqual(blocked, 4)

        change.reload()
        self.assertTrue(change.erpnext_stock_entry)
        se = frappe.get_doc("Stock Entry", change.erpnext_stock_entry)
        self.assertEqual(se.docstatus, 1)
        self.assertEqual(se.items[0].wms_stock_type, "AVAILABLE")
        self.assertEqual(se.items[0].to_wms_stock_type, "WAREHOUSE_BLOCKED")
        erp_qty = flt(frappe.db.get_value("Bin", {"warehouse": self.wh.erpnext_warehouse, "item_code": self.item}, "actual_qty"))
        self.assertEqual(erp_qty, 10)  # same-warehouse dimension-only change must not alter total on-hand

        cancel_posting_change(change.name)
        change.reload()
        self.assertEqual(change.status, "Cancelled")
        available_after = flt(frappe.db.get_value("WMS Stock Balance", {"warehouse": self.warehouse, "product": self.item, "storage_bin": bin1, "handling_unit": hu.name, "stock_type": "AVAILABLE"}, "quantity"))
        self.assertEqual(available_after, 10)
