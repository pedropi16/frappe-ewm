import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.bin_rules import bin_violations, validate_destination_bin
from frappe_wms.services.determination import determine_destination_bin


class TestBinTypesAndGroups(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-BTYPE-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-RACK"
        cls.st2 = f"{cls.wh}-OTHER"
        for code, name in (("RACK", cls.st), ("OTHER", cls.st2)):
            if not frappe.db.exists("Storage Type", name):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": code, "storage_type_name": code, "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for code, l, w, h, hus in (("BT-PALLET", 1200, 800, 1500, 1), ("BT-SHELF", 600, 400, 400, 4)):
            if not frappe.db.exists("Bin Type", code):
                frappe.get_doc({"doctype": "Bin Type", "bin_type_code": code, "bin_type_name": code, "length": l, "width": w, "height": h, "maximum_hus": hus, "active": 1}).insert(ignore_permissions=True)
        for code, l, w, h in (("HT-PALLET", 1200, 800, 150), ("HT-BOX", 400, 300, 300), ("HT-NODIM", 0, 0, 0)):
            if not frappe.db.exists("Handling Unit Type", code):
                frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": code, "hu_type_name": code, "length": l, "width": w, "height": h}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Group", f"{cls.st}-AISLE1"):
            frappe.get_doc({"doctype": "Storage Group", "storage_type": cls.st, "group_code": "AISLE1", "group_name": "Aisle 1"}).insert(ignore_permissions=True)
        cls.bins = {}
        for code, bin_type, group in (("PAL", "BT-PALLET", None), ("SHELF", "BT-SHELF", f"{cls.st}-AISLE1")):
            name = f"{cls.wh}-{code}"
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "bin_type": bin_type, "storage_group": group, "active": 1, "sequence": 1 if code == "PAL" else 2}).insert(ignore_permissions=True)
            cls.bins[code] = name

    def test_hu_type_fits_by_size_not_by_a_list_of_allowed_hu_types(self):
        shelf, pallet = self.bins["SHELF"], self.bins["PAL"]
        self.assertTrue(any("does not fit" in r for r in bin_violations(shelf, hu_type="HT-PALLET", destination_hu="X")))
        self.assertFalse(any("does not fit" in r for r in bin_violations(shelf, hu_type="HT-BOX", destination_hu="X")))
        self.assertFalse(any("does not fit" in r for r in bin_violations(pallet, hu_type="HT-PALLET", destination_hu="X")))
        self.assertFalse(any("does not fit" in r for r in bin_violations(shelf, hu_type="HT-NODIM", destination_hu="X")), "no size on the HU type: nothing to check")
        with self.assertRaises(frappe.ValidationError): validate_destination_bin(shelf, hu_type="HT-PALLET", destination_hu="X")

    def test_capacity_comes_from_the_bin_type_unless_the_bin_sets_its_own(self):
        pallet = self.bins["PAL"]  # bin type: one HU
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "HT-PALLET", "warehouse": self.wh, "current_bin": pallet, "status": "Open"}).insert(ignore_permissions=True)
        self.assertIn("HU count capacity exceeded", bin_violations(pallet, hu_type="HT-PALLET", destination_hu="Y"))
        frappe.db.set_value("Storage Bin", pallet, "maximum_hus", 2)
        self.assertNotIn("HU count capacity exceeded", bin_violations(pallet, hu_type="HT-PALLET", destination_hu="Y"))
        frappe.db.set_value("Storage Bin", pallet, "maximum_hus", 0)
        hu.delete()

    def test_storage_group_must_belong_to_the_bins_storage_type(self):
        group = frappe.get_doc({"doctype": "Storage Group", "storage_type": self.st2, "group_code": frappe.generate_hash(length=4), "group_name": "Elsewhere"}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": f"{self.wh}-BAD", "warehouse": self.wh, "storage_type": self.st, "storage_group": group.name, "active": 1, "sequence": 3}).insert(ignore_permissions=True)

    def test_bin_determination_rule_can_target_a_storage_group(self):
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.wh, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": self.st,
            "destination_storage_group": f"{self.st}-AISLE1", "strategy": "Bin Sequence"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: rule.db_set("active", 0))
        # the pallet bin has the lower sequence but is not in the group
        chosen = determine_destination_bin({"warehouse": self.wh, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": "HT-BOX", "destination_hu": "Z"})
        self.assertEqual(chosen, self.bins["SHELF"])

    def _product(self, indicators):
        item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        name = frappe.db.get_value("WMS Product", {"item": item}) or frappe.get_doc({"doctype": "WMS Product", "item": item, "stock_uom": frappe.db.get_value("Item", item, "stock_uom"), "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True).name
        product = frappe.get_doc("WMS Product", name)
        before = [r.indicator for r in product.handling_indicators]
        product.set("handling_indicators", [{"indicator": i} for i in indicators])
        product.save(ignore_permissions=True)
        self.addCleanup(lambda: (product.reload(), product.set("handling_indicators", [{"indicator": i} for i in before]), product.save(ignore_permissions=True)))
        return item

    def _indicator(self, code, **kw):
        return frappe.db.get_value("Handling Indicator", code) or frappe.get_doc({"doctype": "Handling Indicator", "indicator_code": code, "indicator_name": code, **kw}).insert(ignore_permissions=True).name

    def test_handling_indicator_restricts_a_product_to_its_storage_group(self):
        cold = self._indicator("TEST-COLD", required_storage_group=f"{self.st}-AISLE1")
        item = self._product([cold])
        self.assertTrue(any("handling indicator" in r for r in bin_violations(self.bins["PAL"], item=item)), "the pallet bin is not in the cold group")
        self.assertEqual([r for r in bin_violations(self.bins["SHELF"], item=item) if "handling indicator" in r], [])
        with self.assertRaises(frappe.ValidationError): validate_destination_bin(self.bins["PAL"], item=item)

    def test_two_indicators_with_different_groups_are_refused_on_the_product(self):
        other = frappe.get_doc({"doctype": "Storage Group", "storage_type": self.st, "group_code": frappe.generate_hash(length=4), "group_name": "Other"}).insert(ignore_permissions=True)
        a, b = self._indicator("TEST-G1", required_storage_group=f"{self.st}-AISLE1"), self._indicator("TEST-G2", required_storage_group=other.name)
        with self.assertRaises(frappe.ValidationError): self._product([a, b])

    def test_do_not_unpack_blocks_repacking_but_not_moving_the_whole_hu(self):
        from frappe_wms.services.handling_indicators import check_unpack
        item = self._product([self._indicator("TEST-NOUNPACK", no_unpack=1)])
        with self.assertRaises(frappe.ValidationError): check_unpack(item, "HU-A", "HU-B")
        with self.assertRaises(frappe.ValidationError): check_unpack(item, "HU-A", None)
        check_unpack(item, "HU-A", "HU-A")  # the whole HU travels
        check_unpack(item, None, "HU-B")    # packing loose stock into an HU is fine

    def test_do_not_unpack_is_enforced_when_a_task_is_confirmed(self):
        from frappe_wms.services.stock import post_entries
        from frappe_wms.services.task import create_and_confirm_move
        item = self._product([self._indicator("TEST-NOUNPACK", no_unpack=1)])
        uom = frappe.db.get_value("Item", item, "stock_uom")
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "HT-BOX", "warehouse": self.wh, "current_bin": self.bins["SHELF"], "status": "Open"}).insert(ignore_permissions=True)
        post_entries([{"warehouse": self.wh, "product": item, "handling_unit": hu.name, "storage_bin": self.bins["SHELF"], "stock_type": "AVAILABLE", "stock_uom": uom, "quantity": 5, "movement_type": "701"}],
            "Handling Unit", hu.name, f"test-nounpack:{hu.name}")
        kwargs = dict(warehouse=self.wh, product=item, stock_uom=uom, stock_type="AVAILABLE", source_bin=self.bins["SHELF"], source_hu=hu.name, destination_bin=self.bins["PAL"])
        frappe.db.savepoint("nounpack")  # a failed request rolls back; the new destination HU it registered must not linger here
        with self.assertRaisesRegex(frappe.ValidationError, "must not be unpacked"):
            create_and_confirm_move(quantity=2, destination_hu=frappe.generate_hash(length=10), **kwargs)  # into another HU: unpacking
        frappe.db.rollback(save_point="nounpack")
        create_and_confirm_move(quantity=5, **kwargs)  # the whole HU travels with its stock
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), self.bins["PAL"])


class TestLayoutStorageControl(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-LOSC-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        cls.types = {}
        for code in ("SRC", "INT", "DST"):
            name = f"{cls.wh}-{code}"
            cls.types[code] = name
            if not frappe.db.exists("Storage Type", name):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": code, "storage_type_name": code, "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Storage Group", f"{cls.types['INT']}-END"):
            frappe.get_doc({"doctype": "Storage Group", "storage_type": cls.types["INT"], "group_code": "END", "group_name": "Aisle end"}).insert(ignore_permissions=True)
        cls.bins = {}
        for code, st, group, seq in (("S1", "SRC", None, 1), ("I1", "INT", None, 1), ("I2", "INT", f"{cls.types['INT']}-END", 2), ("D1", "DST", None, 1)):
            name = f"{cls.wh}-{code}"
            cls.bins[code] = name
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.types[st], "storage_group": group, "active": 1, "sequence": seq}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-LOSC-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-LOSC-PALLET", "hu_type_name": "LOSC Pallet"}).insert(ignore_permissions=True)

    def test_task_goes_through_the_intermediate_bin_of_the_group_then_on_to_the_destination(self):
        from frappe_wms.api.scanner import confirm_task
        from frappe_wms.services.stock import post_entries
        from frappe_wms.services.task import create_tasks_for_request
        rule = frappe.get_doc({"doctype": "Layout Storage Control", "warehouse": self.wh, "priority": 1, "source_storage_type": self.types["SRC"], "destination_storage_type": self.types["DST"],
            "intermediate_storage_type": self.types["INT"], "intermediate_storage_group": f"{self.types['INT']}-END"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: rule.db_set("active", 0))
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-LOSC-PALLET", "warehouse": self.wh, "current_bin": self.bins["S1"], "status": "Open"}).insert(ignore_permissions=True)
        post_entries([{"warehouse": self.wh, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bins["S1"], "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 5, "movement_type": "701"}],
            "Handling Unit", hu.name, f"test-losc:{hu.name}")
        request = frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Internal Move", "warehouse": self.wh, "product": self.item, "requested_quantity": 5, "stock_uom": self.uom,
            "source_bin": self.bins["S1"], "source_hu": hu.name, "destination_bin": self.bins["D1"], "stock_type": "AVAILABLE", "reference_doctype": "User", "reference_name": "Administrator",
            "process_type": "INTERNAL_MOVE", "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
        first = create_tasks_for_request(request.name)
        first_task = frappe.get_doc("Warehouse Task", first)
        self.assertEqual((first_task.destination_bin, first_task.final_destination_bin), (self.bins["I2"], self.bins["D1"]), "the group-less I1 is skipped: the rule names the END group")

        result = confirm_task(first, confirmed_quantity=5)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), self.bins["I2"])
        second = frappe.get_all("Warehouse Task", filters={"predecessor_task": first}, fields=["name", "source_bin", "destination_bin", "source_hu", "planned_quantity", "status"])
        self.assertEqual(len(second), 1)
        self.assertEqual((second[0].source_bin, second[0].destination_bin, second[0].source_hu, second[0].planned_quantity), (self.bins["I2"], self.bins["D1"], hu.name, 5))
        self.assertEqual(frappe.db.get_value("Warehouse Request", request.name, "status"), "In Process", "not done until the goods reach the real destination")
        confirm_task(second[0].name, confirmed_quantity=5)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), self.bins["D1"])
        self.assertEqual(frappe.db.get_value("Warehouse Request", request.name, "status"), "Completed")
        self.assertEqual(frappe.db.get_value("Warehouse Request", request.name, "created_quantity"), 5)
