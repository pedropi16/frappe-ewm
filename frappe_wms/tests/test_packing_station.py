import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.packing_station import (
    close_hu, create_hu, delete_empty_hu, pack_all, pack_by_instruction, pack_hu, pack_product, post_difference, reopen_hu,
    station_overview, take_to_table, unpack_hu,
)
from frappe_wms.api.scanner import confirm_task
from frappe_wms.services.shipping import confirm_hu_loaded, create_shipment
from frappe_wms.tests.bootstrap import TEST_CUSTOMER, TEST_ITEM, TEST_SUPPLIER


def qty_in(hu):
    return flt(frappe.db.sql("select sum(quantity) from `tabWMS Stock Balance` where handling_unit=%s", hu)[0][0])


class TestPackingStation(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.item = TEST_ITEM
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        for code, name in (("PS-PALLET", "Packing Station Pallet"), ("PS-CARTON", "Packing Station Carton")):
            if not frappe.db.exists("Handling Unit Type", code):
                frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": code, "hu_type_name": name}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "PS-BOX"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PS-BOX", "hu_type_name": "Packing Station Box", "numbering_mode": "Internal"}).insert(ignore_permissions=True)

    def setUp(self):
        wh = f"WMS-TEST-PACK-{frappe.generate_hash(length=6).upper()}"
        bins = {k: f"{wh}-{k}" for k in ("RECV", "BULK", "PACK", "STAGE", "DOOR", "PACKIN", "PACKOUT")}
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": wh, "warehouse_name": wh, "company": self.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        for code, role in (("GR", "Receiving"), ("BULK", "Storage"), ("PACK", "Packing"), ("DOOR", "Door")):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": wh, "storage_type_code": code, "storage_type_name": code, "storage_role": role,
                            "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for key, st in (("RECV", "GR"), ("BULK", "BULK"), ("PACK", "PACK"), ("STAGE", "GR"), ("DOOR", "DOOR"), ("PACKIN", "PACK"), ("PACKOUT", "PACK")):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": bins[key], "warehouse": wh, "storage_type": f"{wh}-{st}", "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        w = frappe.get_doc("WMS Warehouse", wh)
        w.default_receiving_bin, w.default_shipping_bin, w.default_difference_bin = bins["RECV"], bins["STAGE"], bins["RECV"]
        w.save(ignore_permissions=True)
        frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": wh, "activity": "Putaway", "active": 1, "priority": 1,
                        "destination_storage_type": f"{wh}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WMS Route", "route_code": f"{wh}-R", "route_name": f"{wh}-R", "origin_warehouse": wh,
                        "default_staging_bin": bins["STAGE"], "default_door": bins["DOOR"], "active": 1}).insert(ignore_permissions=True)
        self.wc = frappe.get_doc({"doctype": "Work Center", "warehouse": wh, "work_center_code": "PACK1", "work_center_name": "Pack table 1",
                                  "bin": bins["PACK"], "active": 1}).insert(ignore_permissions=True).name
        self.wh, self.bins = wh, bins
        self._receive(10)

    def _receive(self, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PS-PALLET", "warehouse": self.wh,
                             "current_bin": self.bins["RECV"], "status": "Open"}).insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh, "supplier": TEST_SUPPLIER,
                              "receiving_bin": self.bins["RECV"], "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": self.wh, "receiving_bin": self.bins["RECV"],
                             "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]}).insert(ignore_permissions=True)
        gr.submit()
        confirm_task(create_putaway(gr.name)["warehouse_tasks"][0], confirmed_quantity=qty)

    def _picked_delivery(self, qty, into_hu=None):
        """A delivery picked into a tote at the packing table (its staging bin is the table)."""
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": self.wh,
                              "customer": TEST_CUSTOMER, "delivery_date": nowdate(), "staging_bin": self.bins["PACK"],
                              "items": [{"line_number": 1, "item": self.item, "requested_quantity": qty, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        task = create_pick_tasks(obd.name)[0]
        tote = into_hu or create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        confirm_task(task, confirmed_quantity=qty, destination_hu=tote)
        return obd.name, tote

    def test_pack_close_ship_and_issue_a_carton(self):
        d1, t1 = self._picked_delivery(6)
        d2, t2 = self._picked_delivery(3)
        carton = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]

        pack_product(self.wc, self.item, 4, carton, source_hu=t1)
        self.assertEqual(frappe.db.get_value("Handling Unit", carton, "outbound_delivery"), d1)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", d1, "packing_status"), "In Process")
        # one HU never mixes deliveries
        with self.assertRaisesRegex(frappe.ValidationError, "cannot mix deliveries"):
            pack_product(self.wc, self.item, 1, carton, source_hu=t2)
        with self.assertRaisesRegex(frappe.ValidationError, "Only 2"):
            pack_product(self.wc, self.item, 5, carton, source_hu=t1)
        pack_all(self.wc, t1, carton)
        self.assertEqual((qty_in(carton), qty_in(t1)), (6, 0))

        close_hu(self.wc, carton, gross_weight=7.5)
        hu = frappe.db.get_value("Handling Unit", carton, ["closed", "status", "gross_weight"], as_dict=True)
        self.assertEqual((hu.closed, hu.status, hu.gross_weight), (1, "Closed", 7.5))
        self.assertEqual(frappe.db.get_value("Outbound Delivery", d1, "packing_status"), "Packed")
        with self.assertRaisesRegex(frappe.ValidationError, "closed"):
            pack_product(self.wc, self.item, 1, carton, source_hu=t2)
        reopen_hu(self.wc, carton)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", d1, "packing_status"), "In Process")
        close_hu(self.wc, carton)

        overview = station_overview(self.wc)
        node = next(n for n in overview["handling_units"] if n["name"] == carton)
        self.assertEqual(node["deliveries"], [d1])

        # the emptied pick tote drops out: the shipment loads the packed carton
        shipment = create_shipment(self.wh, [d1])
        self.assertEqual(frappe.get_all("Shipment Handling Unit", filters={"parent": shipment}, pluck="handling_unit"), [carton])
        confirm_hu_loaded(shipment, carton)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", d1, "goods_issue_status"), "Posted")
        self.assertEqual(qty_in(carton), 0)
        self.assertEqual(frappe.db.get_value("Handling Unit", carton, "status"), "Shipped")

    def test_pack_a_tote_onto_a_pallet_ships_the_pallet(self):
        d2, t2 = self._picked_delivery(3)
        pallet = create_hu(self.wc, "PS-PALLET", frappe.generate_hash(length=10))["name"]
        pack_hu(self.wc, t2, pallet)
        self.assertEqual(frappe.db.get_value("Handling Unit", t2, "parent_hu"), pallet)
        self.assertEqual(frappe.db.get_value("Handling Unit", pallet, "outbound_delivery"), d2)
        # unpack and pack again round-trips
        unpack_hu(self.wc, t2)
        pack_hu(self.wc, t2, pallet)
        close_hu(self.wc, pallet)
        self.assertEqual(frappe.db.get_value("Handling Unit", t2, "closed"), 1)
        shipment = create_shipment(self.wh, [d2])
        self.assertEqual(frappe.get_all("Shipment Handling Unit", filters={"parent": shipment}, pluck="handling_unit"), [pallet])
        confirm_hu_loaded(shipment, pallet)
        self.assertEqual(frappe.db.get_value("Outbound Delivery", d2, "goods_issue_status"), "Posted")
        self.assertEqual(qty_in(t2), 0)

    def test_cluster_tote_needs_a_delivery_choice_and_guards(self):
        d3, tote = self._picked_delivery(2)
        d4, _ = self._picked_delivery(3, into_hu=tote)
        carton = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        with self.assertRaisesRegex(frappe.ValidationError, "several deliveries"):
            pack_product(self.wc, self.item, 2, carton, source_hu=tote)
        pack_product(self.wc, self.item, 2, carton, source_hu=tote, outbound_delivery=d3)
        self.assertEqual(frappe.db.get_value("Handling Unit", carton, "outbound_delivery"), d3)
        # stock not at the table, an HU elsewhere, closing an empty HU
        empty = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        with self.assertRaisesRegex(frappe.ValidationError, "empty"):
            close_hu(self.wc, empty)
        frappe.db.set_value("Handling Unit", empty, "current_bin", self.bins["STAGE"])
        with self.assertRaisesRegex(frappe.ValidationError, "not at this packing station"):
            pack_product(self.wc, self.item, 1, empty, source_hu=tote, outbound_delivery=d4)
        with self.assertRaisesRegex(frappe.ValidationError, "No "):
            pack_product(self.wc, "NO-SUCH-ITEM", 1, carton, source_hu=tote)


    # ------------------------------------------------------------------ work center customizing

    def configure(self, **values):
        frappe.db.set_value("Work Center", self.wc, values)

    def test_switched_off_functions_are_refused(self):
        self.configure(allow_pack_hu=0, allow_create_hu=0)
        with self.assertRaisesRegex(frappe.ValidationError, "switched off"):
            create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))
        self.configure(allow_create_hu=1)
        a = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        b = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        with self.assertRaisesRegex(frappe.ValidationError, "switched off"):
            pack_hu(self.wc, a, b)
        self.assertEqual(station_overview(self.wc)["work_center"]["allow_pack_hu"], 0)

    def test_weighing_rules_completeness_check_and_follow_up(self):
        d1, tote = self._picked_delivery(6)
        c1 = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        c2 = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        pack_product(self.wc, self.item, 4, c1, source_hu=tote)
        pack_product(self.wc, self.item, 2, c2, source_hu=tote)
        self.configure(weigh_on_close="Required", weight_tolerance_percent=10, completeness_check="Warn",
                       close_follow_up="Move to Outbound Section", outbound_section_bin=self.bins["PACKOUT"])
        with self.assertRaisesRegex(frappe.ValidationError, "requires the gross weight"):
            close_hu(self.wc, c1)
        frappe.db.set_value("Handling Unit", c1, "gross_weight", 10)   # "calculated" gross
        with self.assertRaisesRegex(frappe.ValidationError, "Tolerance|away from the calculated"):
            close_hu(self.wc, c1, gross_weight=12)
        r = close_hu(self.wc, c1, gross_weight=10.5)
        self.assertEqual(r.get("needs_confirmation"), 1, "the rest of the delivery is still unpacked in c2")
        self.configure(completeness_check="Block")
        with self.assertRaisesRegex(frappe.ValidationError, "not completely packed"):
            close_hu(self.wc, c1, gross_weight=10.5)
        self.configure(completeness_check="Warn")
        r = close_hu(self.wc, c1, gross_weight=10.5, confirm_incomplete=1)
        self.assertEqual(r["moved_to"], self.bins["PACKOUT"])
        self.assertEqual(frappe.db.get_value("Handling Unit", c1, ["current_bin", "gross_weight", "closed"]), (self.bins["PACKOUT"], 10.5, 1))

    def test_pack_by_instruction_splits_into_new_hus(self):
        d1, tote = self._picked_delivery(7)
        spec = frappe.db.get_value("Packaging Spec", {"item": self.item})
        if spec:
            frappe.delete_doc("Packaging Spec", spec, ignore_permissions=True, force=True)
        frappe.get_doc({"doctype": "Packaging Spec", "item": self.item, "active": 1,
                        "levels": [{"level_name": "Box", "quantity_per_level": 3, "hu_type": "PS-BOX"}]}).insert(ignore_permissions=True)
        try:
            self.assertEqual(station_overview(self.wc)["instructions"][self.item][0]["quantity_per_level"], 3)
            r = pack_by_instruction(self.wc, self.item, source_hu=tote, close=1)
            self.assertEqual(len(r["handling_units"]), 3)
            self.assertEqual([qty_in(h) for h in r["handling_units"]], [3, 3, 1])
            self.assertTrue(all(frappe.db.get_value("Handling Unit", h, "outbound_delivery") == d1 for h in r["handling_units"]))
            self.assertTrue(all(frappe.db.get_value("Handling Unit", h, "closed") for h in r["handling_units"]))
        finally:
            frappe.delete_doc("Packaging Spec", frappe.db.get_value("Packaging Spec", {"item": self.item}), ignore_permissions=True, force=True)

    def test_inbound_section_delete_empty_and_difference_for_free_stock(self):
        self.configure(inbound_section_bin=self.bins["PACKIN"], allow_differences=1)
        arriving = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PS-CARTON",
                                   "warehouse": self.wh, "current_bin": self.bins["PACKIN"], "status": "Open"}).insert(ignore_permissions=True).name
        self.assertIn(arriving, [a["name"] for a in station_overview(self.wc)["arriving"]])
        take_to_table(self.wc, arriving)
        self.assertEqual(frappe.db.get_value("Handling Unit", arriving, "current_bin"), self.bins["PACK"])
        delete_empty_hu(self.wc, arriving)
        self.assertEqual(frappe.db.get_value("Handling Unit", arriving, "status"), "Cancelled")

        # free (unpicked) stock on the table: move 2 of it in, then report 1 missing
        box = create_hu(self.wc, "PS-CARTON", frappe.generate_hash(length=10))["name"]
        from frappe_wms.services.stock import transfer_stock
        bulk = frappe.db.get_value("WMS Stock Balance", {"warehouse": self.wh, "storage_bin": self.bins["BULK"], "quantity": [">", 0]},
                                   ["handling_unit", "batch_no"], as_dict=True)
        transfer_stock(source={"warehouse": self.wh, "product": self.item, "batch_no": bulk.batch_no, "serial_no": None, "handling_unit": bulk.handling_unit,
                               "storage_bin": self.bins["BULK"], "stock_type": "AVAILABLE", "stock_uom": self.uom},
                       destination={"handling_unit": box, "storage_bin": self.bins["PACK"], "stock_type": "AVAILABLE"},
                       quantity=2, movement_type="301", reference_doctype="Work Center", reference_name=self.wc, idempotency_key=frappe.generate_hash())
        r = post_difference(self.wc, self.item, 1, source_hu=box, remarks="dropped")
        self.assertEqual(qty_in(box), 1)
        self.assertEqual(frappe.db.get_value("WMS Task Difference", r["difference"], ["direction", "difference_quantity"]), ("Short", 1))
        d1, tote = self._picked_delivery(2)
        with self.assertRaisesRegex(frappe.ValidationError, "picked for"):
            post_difference(self.wc, self.item, 1, source_hu=tote)
