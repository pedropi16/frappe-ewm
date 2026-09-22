import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from frappe_wms.services.kitting import create_kitting_order, complete_kitting_order


class TestKitting(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "KIT-TEST-WH"
        cls.work_center_bin = f"{cls.warehouse}-WC"
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.uom = "Nos"
        cls.item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": cls.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-WC"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "WC", "storage_type_name": "Work Center", "storage_role": "Packing", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.work_center_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.work_center_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-WC", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "KIT-TEST-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "KIT-TEST-PALLET", "hu_type_name": "Kit Test Pallet"}).insert(ignore_permissions=True)

    def _make_bom_set(self, suffix):
        # Each stock-mutating test gets its own dedicated items/BOM - other test methods in
        # this class leave leftover stock behind (no rollback between methods), which would
        # otherwise make a later test's components look more (or less) stocked than intended.
        rm1, rm2, fg = f"KIT-TEST-RM1-{suffix}", f"KIT-TEST-RM2-{suffix}", f"KIT-TEST-FG-{suffix}"
        for item_code in (rm1, rm2, fg):
            if not frappe.db.exists("Item", item_code):
                frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": self.item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
            if not frappe.db.exists("WMS Product", {"item": item_code}):
                frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("BOM", {"item": fg, "docstatus": 1}):
            bom = frappe.get_doc({"doctype": "BOM", "item": fg, "quantity": 1, "company": self.company, "with_operations": 0,
                "items": [{"item_code": rm1, "qty": 2, "uom": self.uom, "stock_uom": self.uom}, {"item_code": rm2, "qty": 3, "uom": self.uom, "stock_uom": self.uom}]})
            bom.insert(ignore_permissions=True)
            bom.submit()
            bom_name = bom.name
        else:
            bom_name = frappe.get_all("BOM", filters={"item": fg, "docstatus": 1}, pluck="name")[0]
        return rm1, rm2, fg, bom_name

    def _seed(self, item, qty):
        # A real Goods Receipt (not a raw ledger post) so ERPNext's own side also has
        # valuated stock for this item/warehouse - Kitting Order's ERPNext mirror (a Repack
        # Stock Entry) needs a resolvable valuation rate for whatever it consumes.
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "KIT-TEST-PALLET", "warehouse": self.warehouse, "current_bin": self.work_center_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.work_center_bin,
            "items": [{"line_number": 1, "item": item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.work_center_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()

    def _balance(self, item):
        # Summed across every balance row (loose + any specific HU) - kitting output posts
        # loose while seeded input stock is HU-tied, so a single-row lookup would miss one.
        total = frappe.db.sql("select coalesce(sum(quantity), 0) from `tabWMS Stock Balance` where warehouse=%s and product=%s and storage_bin=%s and stock_type='AVAILABLE'",
            (self.warehouse, item, self.work_center_bin))[0][0]
        return flt(total)

    def test_create_kitting_order_explodes_bom_with_scaled_quantities(self):
        rm1, rm2, fg, bom_name = self._make_bom_set("A")
        name = create_kitting_order(fg, bom_name, self.warehouse, self.work_center_bin, 5, "Assemble")
        order = frappe.get_doc("Kitting Order", name)
        self.assertEqual(len(order.components), 2)
        by_item = {c.item: c.required_qty for c in order.components}
        self.assertEqual(by_item[rm1], 10)
        self.assertEqual(by_item[rm2], 15)

    def test_assemble_consumes_components_and_produces_kit_item(self):
        rm1, rm2, fg, bom_name = self._make_bom_set("B")
        self._seed(rm1, 20)
        self._seed(rm2, 20)
        name = create_kitting_order(fg, bom_name, self.warehouse, self.work_center_bin, 5, "Assemble")
        result = complete_kitting_order(name)
        self.assertEqual(result["status"], "Completed")

        self.assertEqual(self._balance(rm1), 10)  # 20 - (2*5)
        self.assertEqual(self._balance(rm2), 5)   # 20 - (3*5)
        self.assertEqual(self._balance(fg), 5)

        order = frappe.get_doc("Kitting Order", name)
        self.assertTrue(order.erpnext_stock_entry)
        se = frappe.get_doc("Stock Entry", order.erpnext_stock_entry)
        self.assertEqual(se.stock_entry_type, "Repack")
        self.assertEqual(se.docstatus, 1)

    def test_disassemble_reverses_assembly(self):
        rm1, rm2, fg, bom_name = self._make_bom_set("C")
        self._seed(fg, 5)
        name = create_kitting_order(fg, bom_name, self.warehouse, self.work_center_bin, 5, "Disassemble")
        complete_kitting_order(name)

        self.assertEqual(self._balance(fg), 0)
        self.assertEqual(self._balance(rm1), 10)
        self.assertEqual(self._balance(rm2), 15)

    def test_insufficient_component_stock_throws_before_any_partial_transfer(self):
        rm1, rm2, fg, bom_name = self._make_bom_set("D")
        self._seed(rm1, 100)  # plenty of rm1, none of rm2
        rm1_before = self._balance(rm1)
        name = create_kitting_order(fg, bom_name, self.warehouse, self.work_center_bin, 5, "Assemble")
        with self.assertRaises(frappe.ValidationError):
            complete_kitting_order(name)
        order = frappe.get_doc("Kitting Order", name)
        self.assertEqual(order.status, "Open")
        self.assertEqual(self._balance(rm1), rm1_before, "rm1's own consumption must not stick when a later component fails")
