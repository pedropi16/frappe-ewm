import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate
from frappe_wms.services.stock import post_entries


class TestWmsProduct(IntegrationTestCase):
    def test_non_stock_item_cannot_be_warehouse_managed(self):
        item = frappe.get_all("Item", filters={"is_stock_item": 0}, limit=1, pluck="name")
        if not item:
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            doc = frappe.get_doc({"doctype": "Item", "item_code": "WMS-TEST-NONSTOCK", "item_group": item_group, "is_stock_item": 0, "stock_uom": "Nos"})
            doc.insert(ignore_permissions=True)
            item = [doc.name]
        item = item[0]
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": "Nos", "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)

    def test_stock_uom_is_defaulted_from_item(self):
        item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        item_stock_uom = frappe.db.get_value("Item", item, "stock_uom")
        if frappe.db.exists("WMS Product", {"item": item}):
            self.skipTest("item already has a WMS Product")
        doc = frappe.get_doc({"doctype": "WMS Product", "item": item, "warehouse_managed": 1, "active": 1})
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.stock_uom, item_stock_uom)


class TestWmsProductReceiptControls(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMSPRODUCT-TEST-WH"
        cls.recv_bin = f"{cls.warehouse}-RECV"
        cls.uom = "Nos"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]

        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Type", f"{cls.warehouse}-GR"):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Bin", cls.recv_bin):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": cls.recv_bin, "warehouse": cls.warehouse, "storage_type": f"{cls.warehouse}-GR", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "WMSPRODUCT-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WMSPRODUCT-PALLET", "hu_type_name": "WmsProduct Pallet"}).insert(ignore_permissions=True)

    def _make_item(self, item_code, **product_fields):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1, **product_fields}).insert(ignore_permissions=True)
        return item_code

    def _make_gr(self, item_code, qty=1, serial_no=None, batch_no=None):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "WMSPRODUCT-PALLET", "warehouse": self.warehouse, "current_bin": self.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": self.supplier, "receiving_bin": self.recv_bin,
            "items": [{"line_number": 1, "item": item_code, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item_code, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE", "serial_no": serial_no, "batch_no": batch_no}]})
        gr.insert(ignore_permissions=True)
        return gr

    def test_serial_control_required_at_receipt_is_enforced(self):
        item_code = self._make_item("TEST-WMSPRODUCT-SERIAL", serial_control="Required at Receipt")
        gr = self._make_gr(item_code)
        with self.assertRaises(frappe.ValidationError):
            gr.submit()

    def test_batch_control_is_enforced(self):
        item_code = self._make_item("TEST-WMSPRODUCT-BATCH", batch_control=1)
        gr = self._make_gr(item_code)
        with self.assertRaises(frappe.ValidationError):
            gr.submit()

    def test_shelf_life_expiry_is_stamped_on_receipt(self):
        item_code = self._make_item("TEST-WMSPRODUCT-SLED", shelf_life_days=30)
        gr = self._make_gr(item_code, qty=4)
        gr.submit()
        expiry = frappe.db.get_value("WMS Stock Balance", {"product": item_code, "quantity": [">", 0]}, "shelf_life_expiry_date")
        self.assertEqual(getdate(expiry), getdate(add_days(getdate(gr.posting_datetime), 30)))

    def test_no_shelf_life_days_leaves_expiry_blank(self):
        item_code = self._make_item("TEST-WMSPRODUCT-NOSLED")
        gr = self._make_gr(item_code, qty=2)
        gr.submit()
        expiry = frappe.db.get_value("WMS Stock Balance", {"product": item_code, "quantity": [">", 0]}, "shelf_life_expiry_date")
        self.assertFalse(expiry)


class TestWmsProductIssueControls(IntegrationTestCase):
    # serial_control is a per-product setting (None by default - most items are never expected
    # to carry one) with four values: None, Required at Receipt, Required at Issue, Always.
    # "Required at Receipt"/receipt-side "Always" were already covered by
    # TestWmsProductReceiptControls above; "Required at Issue" and issue-side "Always" were
    # declared in the doctype's own option list but never actually checked anywhere (confirmed
    # by grep) - a product configured to require a serial on the way out could ship with none.
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.warehouse = "WMSPRODUCT-ISS-TEST-WH"
        cls.stage_bin = f"{cls.warehouse}-STAGE"
        cls.door_bin = f"{cls.warehouse}-DOOR"
        cls.uom = "Nos"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.warehouse):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.warehouse, "warehouse_name": cls.warehouse, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in ((f"{cls.warehouse}-STAGE", "Staging"), (f"{cls.warehouse}-DOOR", "Door")):
            if not frappe.db.exists("Storage Type", code):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.warehouse, "storage_type_code": code.split("-")[-1], "storage_type_name": code, "storage_role": role, "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((cls.stage_bin, f"{cls.warehouse}-STAGE"), (cls.door_bin, f"{cls.warehouse}-DOOR")):
            if not frappe.db.exists("Storage Bin", bin_name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": cls.warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "WMSPRODUCT-ISS-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "WMSPRODUCT-ISS-PALLET", "hu_type_name": "WmsProduct Issue Pallet"}).insert(ignore_permissions=True)

    def _make_item(self, item_code, **product_fields):
        if not frappe.db.exists("Item", item_code):
            item_group = frappe.get_all("Item Group", limit=1, pluck="name")[0]
            frappe.get_doc({"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": item_code}):
            frappe.get_doc({"doctype": "WMS Product", "item": item_code, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1, **product_fields}).insert(ignore_permissions=True)
        return item_code

    def _make_gi(self, item_code, qty=1, serial_no=None):
        # A minimal fixture focused purely on serial_control validation: an HU already Loaded
        # and sitting in a Door bin (post_goods_issue's own prerequisites), skipping the full
        # pick/ship/load ceremony that isn't what this test is about.
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "WMSPRODUCT-ISS-PALLET", "warehouse": self.warehouse, "current_bin": self.door_bin, "status": "Loaded"})
        hu.insert(ignore_permissions=True)
        # Seed a matching WMS Stock Balance directly via post_entries (WMS-ledger-only is fine
        # here - the "does the serial get required" question this test is about has nothing to
        # do with ERPNext valuation) so post_goods_issue's own stock deduction has something to
        # deduct from.
        post_entries([{"warehouse": self.warehouse, "product": item_code, "batch_no": None, "serial_no": serial_no, "handling_unit": hu.name,
            "storage_bin": self.door_bin, "stock_type": "AVAILABLE", "quantity": qty, "stock_uom": self.uom, "movement_type": "561"}],
            "Handling Unit", hu.name, frappe.generate_hash(length=16))
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item_code, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": self.stage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": item_code, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE", "serial_no": serial_no}]})
        gi.insert(ignore_permissions=True)
        return gi

    def test_serial_control_required_at_issue_is_enforced(self):
        item_code = self._make_item("TEST-WMSPRODUCT-ISS-SERIAL", serial_control="Required at Issue")
        gi = self._make_gi(item_code)
        with self.assertRaises(frappe.ValidationError):
            gi.submit()

    def test_serial_control_always_is_enforced_at_issue_too(self):
        item_code = self._make_item("TEST-WMSPRODUCT-ISS-ALWAYS", serial_control="Always")
        gi = self._make_gi(item_code)
        with self.assertRaises(frappe.ValidationError):
            gi.submit()

    def test_serial_control_none_does_not_require_a_serial_at_issue(self):
        # The default, and what most products should stay on - confirms the fix above only
        # enforces the check for products actually configured to need one. Unlike the
        # enforcement tests above (which throw before ever reaching a real stock movement), this
        # one actually submits, so it needs real ERPNext-mirrored on-hand stock to issue from -
        # seeded via a real Goods Receipt (raw post_entries is WMS-ledger-only and leaves ERPNext
        # with nothing to deduct from, per this project's own documented NegativeStockError
        # pattern), not via _make_gi's lightweight raw-ledger seed.
        item_code = self._make_item("TEST-WMSPRODUCT-ISS-NONE")
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "WMSPRODUCT-ISS-PALLET", "warehouse": self.warehouse, "current_bin": self.door_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "supplier": supplier, "receiving_bin": self.door_bin,
            "items": [{"line_number": 1, "item": item_code, "expected_quantity": 1, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.warehouse, "receiving_bin": self.door_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": item_code, "quantity": 1, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        hu.reload(); hu.flags.wms_service_update = True; hu.status = "Loaded"; hu.save(ignore_permissions=True)

        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.warehouse, "customer": self.customer, "delivery_date": nowdate(), "staging_bin": self.stage_bin,
            "items": [{"line_number": 1, "item": item_code, "requested_quantity": 1, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        gi = frappe.get_doc({"doctype": "Goods Issue", "outbound_delivery": obd.name, "warehouse": self.warehouse, "staging_bin": self.stage_bin,
            "items": [{"outbound_delivery_item": obd.items[0].name, "item": item_code, "quantity": 1, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gi.insert(ignore_permissions=True)
        gi.submit()
        self.assertEqual(gi.status, "Posted")
