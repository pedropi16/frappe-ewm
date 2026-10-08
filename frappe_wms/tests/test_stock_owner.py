import hashlib

import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.stock import _balance_name, post_entries, rebuild_balances, transfer_stock


class TestStockOwner(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-OWN-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 0}).insert(ignore_permissions=True)
        cls.types = {}
        for code, role in (("ST", "Storage"), ("STG", "Staging")):
            name = cls.types[code] = f"{cls.wh}-{code}"
            if not frappe.db.exists("Storage Type", name):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": code, "storage_type_name": code, "storage_role": role, "capacity_check_method": "None", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = {}
        for code, st in (("B1", "ST"), ("B2", "ST"), ("B3", "ST"), ("STAGE", "STG")):
            name = cls.bins[code] = f"{cls.wh}-{code}"
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.types[st], "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        for code in ("OWN-A", "OWN-B", "PETD-1"):
            if not frappe.db.exists("WMS Stock Owner", code):
                frappe.get_doc({"doctype": "WMS Stock Owner", "owner_code": code, "owner_name": code, "partner_type": "Other"}).insert(ignore_permissions=True)
        frappe.flags.wms_owner_stock = None

    def _entry(self, qty, bin_name="B1", **extra):
        return {"warehouse": self.wh, "product": self.item, "storage_bin": self.bins[bin_name], "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": qty, "movement_type": "701", **extra}

    def _post(self, qty, bin_name="B1", **extra):
        post_entries([self._entry(qty, bin_name, **extra)], "Storage Bin", self.bins[bin_name], f"test-own:{frappe.generate_hash(length=8)}")

    def _qty(self, bin_name="B1", owner=None, party=None):
        return frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.bins[bin_name], "product": self.item, "stock_owner": owner or ["in", ["", None]], "entitled_party": party or ["in", ["", None]]}, "quantity") or 0

    def test_the_identity_of_owner_less_balances_is_unchanged(self):
        values = {"warehouse": "W", "product": "P", "batch_no": None, "serial_no": None, "handling_unit": "H", "storage_bin": "B", "stock_type": "AVAILABLE"}
        legacy = hashlib.sha256("W|P|||H|B|AVAILABLE".encode()).hexdigest()
        self.assertEqual(_balance_name(values), legacy)
        self.assertNotEqual(_balance_name({**values, "stock_owner": "OWN-A"}), legacy)

    def test_owner_stock_is_kept_apart_and_moves_keep_their_owner(self):
        self._post(10)
        self._post(6, stock_owner="OWN-A", entitled_party="PETD-1")
        self.assertEqual((self._qty(), self._qty(owner="OWN-A", party="PETD-1")), (10, 6), "two balances, not one")
        transfer_stock(source={"warehouse": self.wh, "product": self.item, "storage_bin": self.bins["B1"], "stock_type": "AVAILABLE", "stock_uom": self.uom, "stock_owner": "OWN-A", "entitled_party": "PETD-1"},
            destination={"storage_bin": self.bins["B2"], "stock_type": "AVAILABLE"}, quantity=4, movement_type="301", reference_doctype="Storage Bin", reference_name=self.bins["B1"], idempotency_key=f"test-own-t:{frappe.generate_hash(length=6)}")
        self.assertEqual((self._qty("B1", "OWN-A", "PETD-1"), self._qty("B2", "OWN-A", "PETD-1"), self._qty("B2")), (2, 4, 0), "the moved stock is still OWN-A's")

    def test_a_move_that_names_no_owner_resolves_it_from_the_stock(self):
        self._post(5, bin_name="B2", stock_owner="OWN-B")
        transfer_stock(source={"warehouse": self.wh, "product": self.item, "storage_bin": self.bins["B2"], "stock_type": "AVAILABLE", "stock_uom": self.uom},
            destination={"storage_bin": self.bins["B1"], "stock_type": "AVAILABLE"}, quantity=3, movement_type="301", reference_doctype="Storage Bin", reference_name=self.bins["B2"], idempotency_key=f"test-own-r:{frappe.generate_hash(length=6)}")
        self.assertEqual((self._qty("B2", "OWN-B"), self._qty("B1", "OWN-B")), (2, 3))

    def test_two_owners_in_one_place_must_be_named_explicitly(self):
        self._post(5, bin_name="B3", stock_owner="OWN-A")
        self._post(5, bin_name="B3", stock_owner="OWN-B")
        entry = self._entry(-2, bin_name="B3")
        with self.assertRaisesRegex(frappe.ValidationError, "several owners"):
            post_entries([entry], "Storage Bin", self.bins["B3"], f"test-own-x:{frappe.generate_hash(length=6)}")
        post_entries([{**entry, "stock_owner": "OWN-B"}], "Storage Bin", self.bins["B3"], f"test-own-y:{frappe.generate_hash(length=6)}")
        self.assertEqual((self._qty("B3", "OWN-A"), self._qty("B3", "OWN-B")), (5, 3))

    def test_a_receipt_takes_the_owner_of_its_inbound_delivery_and_putaway_keeps_it(self):
        from frappe_wms.api.scanner import confirm_task
        from frappe_wms.services.receipt import create_putaway_requests
        from frappe_wms.services.task import create_tasks_for_request
        if not frappe.db.exists("Handling Unit Type", "TEST-OWN-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-OWN-PALLET", "hu_type_name": "OWN Pallet"}).insert(ignore_permissions=True)
        if not frappe.db.exists("Bin Determination Rule", {"warehouse": self.wh, "activity": "Putaway"}):
            frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.wh, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": self.types["ST"], "strategy": "Bin Sequence"}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": self.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": self.item, "stock_uom": self.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-OWN-PALLET", "warehouse": self.wh, "current_bin": self.bins["STAGE"], "status": "Open"}).insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "supplier": self.supplier, "receiving_bin": self.bins["STAGE"],
            "stock_owner": "OWN-A", "entitled_party": "PETD-1", "items": [{"line_number": 1, "item": self.item, "expected_quantity": 7, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.wh, "receiving_bin": self.bins["STAGE"],
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": 7, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr.submit()
        self.assertEqual(frappe.db.get_value("WMS Stock Balance", {"handling_unit": hu.name}, ["stock_owner", "entitled_party", "quantity"], as_dict=True), {"stock_owner": "OWN-A", "entitled_party": "PETD-1", "quantity": 7})
        request = create_putaway_requests(gr.name)[0]
        self.assertEqual(frappe.db.get_value("Warehouse Request", request, "stock_owner"), "OWN-A")
        task = create_tasks_for_request(request)
        self.assertEqual(frappe.db.get_value("Warehouse Task", task, "stock_owner"), "OWN-A")
        confirm_task(task, confirmed_quantity=7)
        row = frappe.db.get_value("WMS Stock Balance", {"handling_unit": hu.name, "quantity": [">", 0]}, ["storage_bin", "stock_owner"], as_dict=True)
        self.assertEqual((row.stock_owner, row.storage_bin != self.bins["STAGE"]), ("OWN-A", True))

    def test_allocation_only_takes_stock_of_the_deliverys_owner(self):
        from frappe_wms.services.allocation import allocate_delivery
        self._post(8, bin_name="B1", stock_owner="OWN-A")
        def delivery(**kw):
            return frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "customer": self.customer, "delivery_date": frappe.utils.nowdate(),
                "staging_bin": self.bins["STAGE"], "items": [{"line_number": 1, "item": self.item, "requested_quantity": 5, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}], **kw}).insert(ignore_permissions=True)
        other = delivery()
        other.submit()
        allocate_delivery(other.name)
        self.assertEqual(frappe.get_all("Stock Allocation", filters={"outbound_delivery": other.name, "stock_owner": "OWN-A"}), [], "a delivery without an owner never takes OWN-A's stock")
        mine = delivery(stock_owner="OWN-A")
        mine.submit()
        allocate_delivery(mine.name)
        allocations = frappe.get_all("Stock Allocation", filters={"outbound_delivery": mine.name}, fields=["stock_owner", "allocated_quantity"])
        self.assertEqual([(a.stock_owner, a.allocated_quantity) for a in allocations], [("OWN-A", 5)])

    def test_rebuilding_balances_from_the_ledger_keeps_owners_apart(self):
        self._post(4, bin_name="STAGE", stock_owner="OWN-A", entitled_party="PETD-1")
        self._post(3, bin_name="STAGE")
        def snapshot():
            out = {}
            for r in frappe.get_all("WMS Stock Balance", filters={"storage_bin": self.bins["STAGE"], "product": self.item}, fields=["stock_owner", "entitled_party", "quantity"]):
                key = (r.stock_owner or None, r.entitled_party or None)
                out[key] = out.get(key, 0) + r.quantity
            return out
        before = snapshot()
        rebuild_balances(warehouse=self.wh, product=self.item)
        after = snapshot()
        self.assertEqual(before, after)
        self.assertEqual(after[("OWN-A", "PETD-1")], 4)

    def test_cross_docking_only_matches_deliveries_of_the_same_owner(self):
        from frappe_wms.services.cross_dock import find_cross_dock_demand
        item = "TEST-OWN-XD-ITEM"
        if not frappe.db.exists("Item", item):
            frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": frappe.get_all("Item Group", limit=1, pluck="name")[0], "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        delivery = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "customer": self.customer, "delivery_date": frappe.utils.nowdate(),
            "staging_bin": self.bins["STAGE"], "stock_owner": "OWN-A", "items": [{"line_number": 1, "item": item, "requested_quantity": 4, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        delivery.submit()
        self.assertEqual(find_cross_dock_demand(self.wh, item, "AVAILABLE", 4), [], "owner-less goods do not serve OWN-A's delivery")
        self.assertEqual([m["delivery"] for m in find_cross_dock_demand(self.wh, item, "AVAILABLE", 4, "OWN-A")], [delivery.name])

    # ------------------------------------------------------------------ country of origin / special stock

    def _attr_item(self):
        """A product of its own, so these tests never mix with stock the other tests leave behind."""
        item = "TEST-ATTR-ITEM"
        if not frappe.db.exists("Item", item):
            frappe.get_doc({"doctype": "Item", "item_code": item, "item_name": item, "item_group": frappe.get_all("Item Group", limit=1, pluck="name")[0], "stock_uom": self.uom, "is_stock_item": 1}).insert(ignore_permissions=True)
        frappe.db.delete("WMS Stock Balance", {"warehouse": self.wh, "product": item})
        frappe.db.delete("WMS Stock Ledger Entry", {"warehouse": self.wh, "product": item})
        return item

    def _apost(self, item, qty, bin_name, **extra):
        post_entries([{**self._entry(qty, bin_name, **extra), "product": item}], "Storage Bin", self.bins[bin_name], f"test-attr:{frappe.generate_hash(length=8)}")

    def _qty_attrs(self, bin_name, **attrs):
        return frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.bins[bin_name], "product": self.attr, **attrs}, "quantity") or 0

    def test_attribute_stock_is_kept_apart_moves_keep_it_and_rebuild_agrees(self):
        values = {"warehouse": "W", "product": "P", "batch_no": None, "serial_no": None, "handling_unit": "H", "storage_bin": "B", "stock_type": "AVAILABLE"}
        self.assertNotEqual(_balance_name({**values, "country_of_origin": "Spain"}), _balance_name(values))
        self.attr = item = self._attr_item()
        self._apost(item, 10, "B3")
        self._apost(item, 6, "B3", country_of_origin="Spain")
        self._apost(item, 2, "B3", special_stock_type="Sales Order", special_stock_ref="SO-X")
        self.assertEqual((self._qty_attrs("B3", country_of_origin=["in", ["", None]], special_stock_ref=["in", ["", None]]), self._qty_attrs("B3", country_of_origin="Spain"), self._qty_attrs("B3", special_stock_ref="SO-X")), (10, 6, 2))
        # a move naming only the country takes it from the Spanish stock; one naming nothing is ambiguous
        src = {"warehouse": self.wh, "product": item, "storage_bin": self.bins["B3"], "stock_type": "AVAILABLE", "stock_uom": self.uom}
        dst = {"storage_bin": self.bins["B1"], "stock_type": "AVAILABLE"}
        kw = dict(movement_type="301", reference_doctype="Storage Bin", reference_name=self.bins["B3"])
        transfer_stock(source={**src, "country_of_origin": "Spain"}, destination=dst, quantity=4, idempotency_key=f"test-attr:{frappe.generate_hash(length=6)}", **kw)
        self.assertEqual((self._qty_attrs("B3", country_of_origin="Spain"), self._qty_attrs("B1", country_of_origin="Spain")), (2, 4), "the moved stock is still Spanish")
        before = {(r.country_of_origin or None, r.special_stock_ref or None, r.storage_bin): r.quantity for r in frappe.get_all("WMS Stock Balance", filters={"product": item, "warehouse": self.wh, "quantity": [">", 0]}, fields=["country_of_origin", "special_stock_ref", "storage_bin", "quantity"])}
        rebuild_balances(warehouse=self.wh, product=item)
        after = {(r.country_of_origin or None, r.special_stock_ref or None, r.storage_bin): r.quantity for r in frappe.get_all("WMS Stock Balance", filters={"product": item, "warehouse": self.wh, "quantity": [">", 0]}, fields=["country_of_origin", "special_stock_ref", "storage_bin", "quantity"])}
        self.assertEqual(before, after)

    def test_special_stock_is_only_for_its_order_and_country_can_be_required(self):
        from frappe_wms.services.allocation import allocate_delivery
        item = self._attr_item()
        self._apost(item, 5, "B2", special_stock_type="Sales Order", special_stock_ref="SO-RES")
        self._apost(item, 5, "B2", country_of_origin="Spain")
        def delivery(qty=4, **line):
            d = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "customer": self.customer, "delivery_date": frappe.utils.nowdate(),
                "staging_bin": self.bins["STAGE"], "items": [{"line_number": 1, "item": item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE", **line}]}).insert(ignore_permissions=True)
            d.submit()
            return d
        taken = lambda d: {(a.special_stock_ref or None, a.country_of_origin or None): a.allocated_quantity for a in frappe.get_all("Stock Allocation", filters={"outbound_delivery": d.name}, fields=["special_stock_ref", "country_of_origin", "allocated_quantity"])}
        plain = delivery(4)
        allocate_delivery(plain.name)
        self.assertNotIn("SO-RES", [k[0] for k in taken(plain)], "reserved stock is never given to someone else's delivery")
        spanish = delivery(3, required_country_of_origin="Spain")
        allocate_delivery(spanish.name)
        self.assertTrue(all(k[1] == "Spain" for k in taken(spanish)), taken(spanish))
        mine = delivery(5)
        frappe.db.set_value("Outbound Delivery Item", mine.items[0].name, "sales_order", None)  # sales_order is a Link: the reservation matches on its name
        frappe.db.sql("update `tabOutbound Delivery Item` set sales_order=%s where name=%s", ("SO-RES", mine.items[0].name))
        allocate_delivery(mine.name)
        self.assertEqual(taken(mine).get(("SO-RES", None)), 5, "the order's own reserved stock is taken first")

    def test_a_receipt_row_takes_origin_and_reservation_from_its_inbound_line(self):
        from frappe_wms.services.receipt import _row_attrs
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "supplier": self.supplier, "receiving_bin": self.bins["STAGE"],
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": 3, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE", "country_of_origin": "Spain", "special_stock_type": "Project", "special_stock_ref": "PRJ-1"}]}).insert(ignore_permissions=True)
        row = frappe._dict(inbound_delivery_item=ind.items[0].name, country_of_origin=None, special_stock_type=None, special_stock_ref=None)
        self.assertEqual(_row_attrs(row), {"country_of_origin": "Spain", "special_stock_type": "Project", "special_stock_ref": "PRJ-1"})
        row.country_of_origin = "France"
        self.assertEqual(_row_attrs(row)["country_of_origin"], "France", "the receipt row's own value wins")
