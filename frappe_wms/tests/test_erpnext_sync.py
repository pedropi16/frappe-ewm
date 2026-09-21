import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate, flt

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
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-DOOR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "DOOR", "storage_type_name": "DOOR", "storage_role": "Door", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
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
        self.assertEqual(se.items[0].to_wms_stock_type, "AVAILABLE")

        putaway = create_putaway(gr.name)
        confirm_task(putaway["warehouse_tasks"][0], confirmed_quantity=7)

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 3, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=3)

        # No manual HU status override here: picking must auto-stage the HU on its own.
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "status"), "Staged")

        # Goods Issue requires the HU to be Loaded and sitting in a Door bin, not merely staged -
        # this test is about the ERPNext sync wiring, not the loading flow itself.
        frappe.db.set_value("Storage Bin", self.stage_bin, "storage_type", f"{self.warehouse}-DOOR")
        frappe.db.set_value("Handling Unit", hu.name, "status", "Loaded")

        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": self.stage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": self.item, "quantity": 3, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gi.insert(ignore_permissions=True)
        gi.submit()

        self.assertTrue(gi.erpnext_stock_entry)
        se2 = frappe.get_doc("Stock Entry", gi.erpnext_stock_entry)
        self.assertEqual(se2.stock_entry_type, "Material Issue")
        self.assertEqual(self._erpnext_qty(), qty_before + 4)
        self.assertEqual(se2.items[0].wms_stock_type, "AVAILABLE")

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

    def _make_item(self, item_code, valuation_rate=0, last_purchase_rate=0):
        if frappe.db.exists("Item", item_code):
            frappe.db.set_value("Item", item_code, {"valuation_rate": valuation_rate, "last_purchase_rate": last_purchase_rate})
            return item_code
        item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
        frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group,
            "stock_uom": self.uom, "is_stock_item": 1, "valuation_rate": valuation_rate, "last_purchase_rate": last_purchase_rate}).insert(ignore_permissions=True)
        return item_code

    def _standalone_receipt(self, item_code, qty=1, uom=None, conversion_factor=None):
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "ERPSYNC-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item_code, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE", "uom": uom, "conversion_factor": conversion_factor}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item_code, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        return frappe.get_doc("Stock Entry", gr.erpnext_stock_entry)

    def test_standalone_receipt_uses_item_valuation_rate(self):
        item_code = self._make_item("TEST-ERPSYNC-RATE-1", valuation_rate=42)
        se = self._standalone_receipt(item_code)
        self.assertEqual(se.items[0].basic_rate, 42)
        self.assertFalse(se.items[0].allow_zero_valuation_rate)

    def test_standalone_receipt_falls_back_to_last_purchase_rate(self):
        item_code = self._make_item("TEST-ERPSYNC-RATE-2", valuation_rate=0, last_purchase_rate=17)
        se = self._standalone_receipt(item_code)
        self.assertEqual(se.items[0].basic_rate, 17)
        self.assertFalse(se.items[0].allow_zero_valuation_rate)

    def test_standalone_receipt_zero_valuation_only_as_last_resort(self):
        item_code = self._make_item("TEST-ERPSYNC-RATE-3", valuation_rate=0, last_purchase_rate=0)
        se = self._standalone_receipt(item_code)
        self.assertFalse(se.items[0].basic_rate)
        self.assertEqual(se.items[0].allow_zero_valuation_rate, 1)

    def test_standalone_issue_never_sets_allow_zero_valuation_rate(self):
        item_code = self._make_item("TEST-ERPSYNC-RATE-4", valuation_rate=0, last_purchase_rate=0)
        self._standalone_receipt(item_code, qty=5)
        balance = frappe.get_all("WMS Stock Balance", filters={"product": item_code, "quantity": [">", 0]}, fields=["storage_bin", "handling_unit"], limit=1)[0]

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": balance.storage_bin,
            "items": [{"line_number": 1, "item": item_code, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()

        # Goods Issue requires the HU to be Loaded and sitting in a Door bin - this test is
        # about the ERPNext sync valuation wiring, not the loading flow itself.
        frappe.db.set_value("Storage Bin", balance.storage_bin, "storage_type", f"{self.warehouse}-DOOR")
        frappe.db.set_value("Handling Unit", balance.handling_unit, "status", "Loaded")

        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": balance.storage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": item_code, "quantity": 5, "stock_uom": self.uom, "handling_unit": balance.handling_unit, "stock_type": "AVAILABLE"}]})
        gi.insert(ignore_permissions=True)
        gi.submit()
        se = frappe.get_doc("Stock Entry", gi.erpnext_stock_entry)
        self.assertFalse(se.items[0].allow_zero_valuation_rate)

    def _ensure_uom(self, uom_name):
        if not frappe.db.exists("UOM", uom_name):
            frappe.get_doc({"doctype": "UOM", "uom_name": uom_name}).insert(ignore_permissions=True)
        return uom_name

    def test_stock_entry_receipt_row_uses_inbound_delivery_transactional_uom(self):
        item_code = self._make_item("TEST-ERPSYNC-UOM-1")
        box = self._ensure_uom("Box of 5")
        # 2 boxes of 5 = 10 units in stock_uom.
        se = self._standalone_receipt(item_code, qty=10, uom=box, conversion_factor=5)
        self.assertEqual(se.items[0].uom, box)
        self.assertEqual(flt(se.items[0].conversion_factor), 5)
        self.assertEqual(flt(se.items[0].qty), 2)
        self.assertEqual(flt(se.items[0].transfer_qty), 10)

    def test_stock_entry_row_defaults_to_stock_uom_when_no_alternate_uom_set(self):
        item_code = self._make_item("TEST-ERPSYNC-UOM-2")
        se = self._standalone_receipt(item_code, qty=7)
        self.assertEqual(se.items[0].uom, self.uom)
        self.assertEqual(flt(se.items[0].conversion_factor), 1)
        self.assertEqual(flt(se.items[0].qty), 7)
        self.assertEqual(flt(se.items[0].transfer_qty), 7)

    def test_stock_entry_issue_row_uses_outbound_delivery_transactional_uom(self):
        item_code = self._make_item("TEST-ERPSYNC-UOM-3")
        self._standalone_receipt(item_code, qty=10)
        balance = frappe.get_all("WMS Stock Balance", filters={"product": item_code, "quantity": [">", 0]}, fields=["storage_bin", "handling_unit"], limit=1)[0]
        box = self._ensure_uom("Box of 2")

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": balance.storage_bin,
            "items": [{"line_number": 1, "item": item_code, "requested_quantity": 10, "stock_uom": self.uom, "required_stock_type": "AVAILABLE", "uom": box, "conversion_factor": 2}]})
        obd.insert(ignore_permissions=True)
        obd.submit()

        frappe.db.set_value("Storage Bin", balance.storage_bin, "storage_type", f"{self.warehouse}-DOOR")
        frappe.db.set_value("Handling Unit", balance.handling_unit, "status", "Loaded")

        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": balance.storage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": item_code, "quantity": 10, "stock_uom": self.uom, "handling_unit": balance.handling_unit, "stock_type": "AVAILABLE"}]})
        gi.insert(ignore_permissions=True)
        gi.submit()
        se = frappe.get_doc("Stock Entry", gi.erpnext_stock_entry)
        self.assertEqual(se.items[0].uom, box)
        self.assertEqual(flt(se.items[0].conversion_factor), 2)
        self.assertEqual(flt(se.items[0].qty), 5)
        self.assertEqual(flt(se.items[0].transfer_qty), 10)
