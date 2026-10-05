import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.bin_rules import bin_violations, validate_destination_bin
from frappe_wms.services.determination import determine_destination_bin


class TestControlIndicators(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-PCI-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        cls.types = {}
        for code, extra in (("HR", {"putaway_strategy": "Bin Sequence"}), ("BULK", {"putaway_strategy": "Bin Sequence"}), ("FLOOR", {"hu_requirement": "Forbidden"}), ("OTHER", {})):
            name = cls.types[code] = f"{cls.wh}-{code}"
            if not frappe.db.exists("Storage Type", name):
                frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": code, "storage_type_name": code, "storage_role": "Storage", "capacity_check_method": "None", "active": 1, **extra}).insert(ignore_permissions=True)
        for code in ("PCI-A", "PCI-B"):
            if not frappe.db.exists("Putaway Control Indicator", code): frappe.get_doc({"doctype": "Putaway Control Indicator", "indicator_code": code, "indicator_name": code}).insert(ignore_permissions=True)
        for doctype, code in (("Stock Removal Control Indicator", "SRCI-A"), ("Storage Section Indicator", "SSI-COLD")):
            if not frappe.db.exists(doctype, code): frappe.get_doc({"doctype": doctype, "indicator_code": code, "indicator_name": code}).insert(ignore_permissions=True)
        for st, code, ind in (("HR", "COLD", "SSI-COLD"), ("HR", "AMBIENT", None)):
            name = f"{cls.types[st]}-{code}"
            if not frappe.db.exists("Storage Section", name):
                frappe.get_doc({"doctype": "Storage Section", "storage_type": cls.types[st], "section_code": code, "section_name": code, "section_indicator": ind}).insert(ignore_permissions=True)
        cls.bins = {}
        for code, st, sec, seq in (("HR-COLD", "HR", "COLD", 1), ("HR-AMB", "HR", "AMBIENT", 2), ("HR-PLAIN", "HR", None, 3), ("BULK1", "BULK", None, 1), ("FLOOR1", "FLOOR", None, 1), ("OTHER1", "OTHER", None, 1)):
            name = cls.bins[code] = f"{cls.wh}-{code}"
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.types[st], "storage_section": f"{cls.types[st]}-{sec}" if sec else None, "active": 1, "sequence": seq}).insert(ignore_permissions=True)
        if not frappe.db.exists("WMS Product", {"item": cls.item}):
            frappe.get_doc({"doctype": "WMS Product", "item": cls.item, "stock_uom": cls.uom, "warehouse_managed": 1, "active": 1}).insert(ignore_permissions=True)
        frappe.clear_cache()  # cached docs of the rolled-back fixtures of an earlier run

    def _product_row(self, **values):
        name = frappe.db.get_value("WMS Product Warehouse", {"item": self.item, "warehouse": self.wh})
        row = frappe.get_doc("WMS Product Warehouse", name) if name else frappe.get_doc({"doctype": "WMS Product Warehouse", "item": self.item, "warehouse": self.wh, "active": 1})
        row.update({"putaway_control_indicator": None, "stock_removal_control_indicator": None, "storage_section_indicator": None, "two_step_picking": 0, "preferred_storage_type": None, **values})
        row.set("storage_type_limits", values.get("storage_type_limits", []))
        row.save(ignore_permissions=True)
        frappe.clear_cache()
        return row

    def _sequence(self, direction, indicator_field, indicator, types):
        for old in frappe.get_all("Storage Type Search Sequence", filters={"warehouse": self.wh, "direction": direction, indicator_field: indicator, "active": 1}, pluck="name"):
            frappe.db.set_value("Storage Type Search Sequence", old, "active", 0)
        return frappe.get_doc({"doctype": "Storage Type Search Sequence", "warehouse": self.wh, "direction": direction, indicator_field: indicator, "active": 1,
            "storage_types": [{"storage_type": t, "sequence": i} for i, t in enumerate(types, 1)]}).insert(ignore_permissions=True)

    def _context(self, **kw):
        return {"warehouse": self.wh, "activity": "Putaway", "item": self.item, "stock_type": "AVAILABLE", "hu_type": None, "destination_hu": "X", **kw}

    def test_putaway_control_indicator_selects_the_search_sequence_and_the_storage_type_default_strategy(self):
        self._product_row(putaway_control_indicator="PCI-A")
        self._sequence("Putaway", "putaway_control_indicator", "PCI-A", [self.types["BULK"], self.types["HR"]])
        self.assertEqual(determine_destination_bin(self._context()), self.bins["BULK1"], "no rule at all: the indicator's first storage type with its default strategy")
        self._product_row(putaway_control_indicator="PCI-B")  # an indicator with no sequence configured
        with self.assertRaises(frappe.ValidationError): determine_destination_bin(self._context())

    def test_bin_determination_rule_still_wins_and_preferred_storage_type_is_the_last_fallback(self):
        self._product_row(putaway_control_indicator="PCI-A", preferred_storage_type=self.types["OTHER"])
        self._sequence("Putaway", "putaway_control_indicator", "PCI-A", [self.types["BULK"]])
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.wh, "activity": "Putaway", "active": 1, "priority": 1, "strategy": "Bin Sequence"}).insert(ignore_permissions=True)
        self.addCleanup(lambda: rule.db_set("active", 0))
        self.assertEqual(determine_destination_bin(self._context()), self.bins["BULK1"], "the rule has no storage type: the indicator's sequence beats the preferred type")
        rule.db_set("destination_storage_type", self.types["HR"])
        self.assertTrue(determine_destination_bin(self._context()).startswith(f"{self.wh}-HR"), "a storage type on the rule beats the indicator")
        self._product_row(preferred_storage_type=self.types["OTHER"])
        rule.db_set("destination_storage_type", None)
        self.assertEqual(determine_destination_bin(self._context()), self.bins["OTHER1"], "no indicator: the preferred storage type, as before")

    def test_storage_section_indicator_reserves_sections_for_the_products_that_carry_it(self):
        self._product_row(preferred_storage_type=self.types["HR"])
        rule = frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": self.wh, "activity": "Putaway", "active": 1, "priority": 1, "strategy": "Bin Sequence", "destination_storage_type": self.types["HR"]}).insert(ignore_permissions=True)
        self.addCleanup(lambda: rule.db_set("active", 0))
        self.assertEqual(determine_destination_bin(self._context()), self.bins["HR-AMB"], "the cold section (lowest sequence) is not for a product without its indicator")
        self._product_row(preferred_storage_type=self.types["HR"], storage_section_indicator="SSI-COLD")
        self.assertEqual(determine_destination_bin(self._context()), self.bins["HR-COLD"])

    def test_maximum_quantity_per_storage_type(self):
        from frappe_wms.services.stock import post_entries
        self._product_row(putaway_control_indicator="PCI-A", storage_type_limits=[{"storage_type": self.types["BULK"], "maximum_quantity": 10}])
        post_entries([{"warehouse": self.wh, "product": self.item, "storage_bin": self.bins["BULK1"], "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 8, "movement_type": "701"}], "Storage Bin", self.bins["BULK1"], f"test-pci-maxq:{frappe.generate_hash(length=6)}")
        self.assertTrue(any("maximum quantity" in r for r in bin_violations(self.bins["BULK1"], item=self.item, incoming_quantity=5)))
        self.assertEqual([r for r in bin_violations(self.bins["BULK1"], item=self.item, incoming_quantity=2) if "maximum quantity" in r], [])
        with self.assertRaises(frappe.ValidationError): validate_destination_bin(self.bins["BULK1"], item=self.item, incoming_quantity=5)
        self._sequence("Putaway", "putaway_control_indicator", "PCI-A", [self.types["BULK"], self.types["HR"]])
        self.assertTrue(determine_destination_bin(self._context(incoming_quantity=5)).startswith(f"{self.wh}-HR"), "the full storage type is skipped, the next one in the sequence is used")

    def test_stock_removal_control_indicator_orders_and_limits_the_storage_types_stock_is_taken_from(self):
        from frappe_wms.services.allocation import _by_removal_sequence
        self._product_row(stock_removal_control_indicator="SRCI-A")
        self._sequence("Removal", "stock_removal_control_indicator", "SRCI-A", [self.types["BULK"], self.types["HR"]])
        balances = [frappe._dict(storage_bin=self.bins[b]) for b in ("HR-PLAIN", "OTHER1", "BULK1")]
        self.assertEqual([b.storage_bin for b in _by_removal_sequence(balances, self.item, self.wh, "AVAILABLE")], [self.bins["BULK1"], self.bins["HR-PLAIN"]])
        self._product_row()
        self.assertEqual(len(_by_removal_sequence(balances, self.item, self.wh, "AVAILABLE")), 3, "no indicator: untouched")

    def test_a_storage_type_that_forbids_handling_units_takes_the_stock_loose(self):
        from frappe_wms.api.scanner import confirm_task
        from frappe_wms.services.stock import post_entries
        from frappe_wms.services.task import create_and_confirm_move
        if not frappe.db.exists("Handling Unit Type", "TEST-PCI-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-PCI-PALLET", "hu_type_name": "PCI Pallet"}).insert(ignore_permissions=True)
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-PCI-PALLET", "warehouse": self.wh, "current_bin": self.bins["HR-PLAIN"], "status": "Open"}).insert(ignore_permissions=True)
        post_entries([{"warehouse": self.wh, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bins["HR-PLAIN"], "stock_type": "AVAILABLE", "stock_uom": self.uom, "quantity": 4, "movement_type": "701"}],
            "Handling Unit", hu.name, f"test-pci-forbid:{hu.name}")
        create_and_confirm_move(warehouse=self.wh, product=self.item, quantity=4, stock_uom=self.uom, stock_type="AVAILABLE", source_bin=self.bins["HR-PLAIN"], source_hu=hu.name, destination_bin=self.bins["FLOOR1"])
        loose = frappe.db.get_value("WMS Stock Balance", {"storage_bin": self.bins["FLOOR1"], "product": self.item, "handling_unit": ["in", ["", None]]}, "quantity")
        self.assertEqual(loose, 4)
        self.assertEqual(frappe.db.get_value("Handling Unit", hu.name, "current_bin"), self.bins["HR-PLAIN"], "the emptied HU stays where it was")

    def test_process_type_defaults_fill_what_the_request_leaves_open(self):
        from frappe_wms.services.task import create_tasks_for_request
        code = f"TESTDEF{frappe.generate_hash(length=4)}".upper()
        frappe.get_doc({"doctype": "Warehouse Process Type", "process_type_code": code, "process_type_name": code, "activity": "Internal Move", "movement_type": "301", "destination_required": 1,
            "default_destination_bin": self.bins["BULK1"], "default_priority": "High"}).insert(ignore_permissions=True)
        request = frappe.get_doc({"doctype": "Warehouse Request", "request_type": "Internal Move", "warehouse": self.wh, "product": self.item, "requested_quantity": 1, "stock_uom": self.uom,
            "source_bin": self.bins["HR-PLAIN"], "stock_type": "AVAILABLE", "reference_doctype": "User", "reference_name": "Administrator", "process_type": code, "priority": "Normal", "status": "Open"}).insert(ignore_permissions=True)
        task = frappe.get_doc("Warehouse Task", create_tasks_for_request(request.name))
        self.assertEqual((task.destination_bin, task.priority), (self.bins["BULK1"], "High"))

    def test_the_products_two_step_flag_overrides_the_single_step_process_type(self):
        from frappe_wms.services.task import _pick_destination
        self._product_row(two_step_picking=1)
        first = frappe._dict(product=self.item, _warehouse=self.wh, _staging_bin=self.bins["HR-PLAIN"], outbound_delivery=None)
        with self.assertRaisesRegex(frappe.ValidationError, "Default Picking Staging Bin"):
            _pick_destination(frappe._dict(picking_strategy="Single-Step"), first)  # reached the two-step branch
        self._product_row()
        self.assertEqual(_pick_destination(frappe._dict(picking_strategy="Single-Step"), first)[0], self.bins["HR-PLAIN"])
