import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from frappe_wms.api.inbound import create_putaway
from frappe_wms.api.outbound import allocate_delivery, create_pick_tasks
from frappe_wms.api.scanner import confirm_task
from frappe_wms.api.printing import list_queued_spools, mark_printed, mark_failed
from frappe_wms.services.shipping import create_shipment, confirm_hu_loaded


class TestPrinting(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        cls.supplier = frappe.get_all("Supplier", limit=1, pluck="name")[0]
        cls.customer = frappe.get_all("Customer", limit=1, pluck="name")[0]
        cls.company = frappe.get_all("Company", limit=1, pluck="name")[0]
        if not frappe.db.exists("Handling Unit Type", "PRINTTEST-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "PRINTTEST-PALLET", "hu_type_name": "Print Test Pallet"}).insert(ignore_permissions=True)

    def _new_scenario(self, with_printer_rules=()):
        # with_printer_rules: which events (from WMS Print Determination Rule's options) should
        # have an active rule configured for this warehouse, pointed at one Printer resource.
        warehouse = f"WMS-TEST-PRINT-{frappe.generate_hash(length=6).upper()}"
        recv_bin, bulk_bin, stage_bin, door_bin = f"{warehouse}-RECV", f"{warehouse}-BULK", f"{warehouse}-STAGE", f"{warehouse}-DOOR"
        frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": warehouse, "warehouse_name": warehouse, "company": self.company, "default_stock_type": "AVAILABLE"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "GR", "storage_type_name": "GR", "storage_role": "Receiving", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "BULK", "storage_type_name": "BULK", "storage_role": "Storage", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "Storage Type", "warehouse": warehouse, "storage_type_code": "DOOR", "storage_type_name": "DOOR", "storage_role": "Door", "capacity_check_method": "HU Count", "active": 1}).insert(ignore_permissions=True)
        for bin_name, st in ((recv_bin, f"{warehouse}-GR"), (bulk_bin, f"{warehouse}-BULK"), (stage_bin, f"{warehouse}-GR"), (door_bin, f"{warehouse}-DOOR")):
            frappe.get_doc({"doctype": "Storage Bin", "bin_code": bin_name, "warehouse": warehouse, "storage_type": st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        wh = frappe.get_doc("WMS Warehouse", warehouse)
        wh.default_receiving_bin = recv_bin
        wh.default_shipping_bin = stage_bin
        wh.default_difference_bin = recv_bin
        wh.save(ignore_permissions=True)
        frappe.get_doc({"doctype": "Bin Determination Rule", "warehouse": warehouse, "activity": "Putaway", "active": 1, "priority": 1, "destination_storage_type": f"{warehouse}-BULK", "strategy": "Least Utilized Bin"}).insert(ignore_permissions=True)
        frappe.get_doc({"doctype": "WMS Route", "route_code": f"{warehouse}-ROUTE", "route_name": f"{warehouse}-ROUTE",
            "origin_warehouse": warehouse, "default_staging_bin": stage_bin, "default_door": door_bin, "active": 1}).insert(ignore_permissions=True)

        printer = frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8), "warehouse": warehouse,
            "resource_type": "Printer", "device_id": "LABEL-01", "active": 1})
        printer.insert(ignore_permissions=True)
        for i, event in enumerate(with_printer_rules, 1):
            frappe.get_doc({"doctype": "WMS Print Determination Rule", "priority": i, "warehouse": warehouse,
                "event": event, "output_device": printer.name, "active": 1}).insert(ignore_permissions=True)

        return frappe._dict(warehouse=warehouse, recv_bin=recv_bin, bulk_bin=bulk_bin, stage_bin=stage_bin, door_bin=door_bin, printer=printer.name)

    def test_no_spool_is_created_without_a_matching_rule(self):
        scenario = self._new_scenario()
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_type": "PRINTTEST-PALLET", "hu_number": frappe.generate_hash(length=10),
            "warehouse": scenario.warehouse, "current_bin": scenario.recv_bin}).insert(ignore_permissions=True)
        self.assertFalse(frappe.db.exists("WMS Print Spool", {"reference_doctype": "Handling Unit", "reference_name": hu.name}))

    def test_hu_created_queues_a_print_spool_when_a_rule_is_configured(self):
        scenario = self._new_scenario(with_printer_rules=["HU Created"])
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_type": "PRINTTEST-PALLET", "hu_number": frappe.generate_hash(length=10),
            "warehouse": scenario.warehouse, "current_bin": scenario.recv_bin}).insert(ignore_permissions=True)

        spool = frappe.get_all("WMS Print Spool", filters={"reference_doctype": "Handling Unit", "reference_name": hu.name},
            fields=["name", "status", "output_device", "warehouse", "event"])
        self.assertEqual(len(spool), 1)
        self.assertEqual(spool[0].status, "Queued")
        self.assertEqual(spool[0].output_device, scenario.printer)
        self.assertEqual(spool[0].warehouse, scenario.warehouse)

        rows = list_queued_spools(warehouse=scenario.warehouse)
        self.assertTrue(any(r["name"] == spool[0].name for r in rows))

        result = mark_printed(spool[0].name)
        self.assertEqual(result["status"], "Printed")
        self.assertEqual(frappe.db.get_value("WMS Print Spool", spool[0].name, "status"), "Printed")
        self.assertIsNotNone(frappe.db.get_value("WMS Print Spool", spool[0].name, "printed_at"))

        with self.assertRaises(frappe.ValidationError):
            mark_printed(spool[0].name)

    def test_mark_failed_records_a_reason(self):
        scenario = self._new_scenario(with_printer_rules=["HU Created"])
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_type": "PRINTTEST-PALLET", "hu_number": frappe.generate_hash(length=10),
            "warehouse": scenario.warehouse, "current_bin": scenario.recv_bin}).insert(ignore_permissions=True)
        spool_name = frappe.get_all("WMS Print Spool", filters={"reference_doctype": "Handling Unit", "reference_name": hu.name}, pluck="name")[0]

        mark_failed(spool_name, reason="Printer offline")
        self.assertEqual(frappe.db.get_value("WMS Print Spool", spool_name, "status"), "Failed")
        self.assertEqual(frappe.db.get_value("WMS Print Spool", spool_name, "remarks"), "Printer offline")

    def test_print_determination_rule_requires_a_printer_resource_in_the_same_warehouse(self):
        scenario = self._new_scenario()
        operator = frappe.get_doc({"doctype": "WMS Resource", "resource_code": frappe.generate_hash(length=8), "warehouse": scenario.warehouse,
            "resource_type": "Operator", "active": 1}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "WMS Print Determination Rule", "priority": 1, "warehouse": scenario.warehouse,
                "event": "HU Created", "output_device": operator.name, "active": 1}).insert(ignore_permissions=True)

        other_scenario = self._new_scenario()
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": "WMS Print Determination Rule", "priority": 1, "warehouse": scenario.warehouse,
                "event": "HU Created", "output_device": other_scenario.printer, "active": 1}).insert(ignore_permissions=True)

    def _receive_and_putaway(self, scenario, qty):
        hu = frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "PRINTTEST-PALLET", "warehouse": scenario.warehouse, "current_bin": scenario.recv_bin, "status": "Open"})
        hu.insert(ignore_permissions=True)
        ind = frappe.get_doc({"doctype": "Inbound Delivery", "inbound_delivery_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse, "supplier": self.supplier, "receiving_bin": scenario.recv_bin,
            "items": [{"line_number": 1, "item": self.item, "expected_quantity": qty, "stock_uom": self.uom, "expected_stock_type": "AVAILABLE"}]})
        ind.insert(ignore_permissions=True)
        gr = frappe.get_doc({"doctype": "Goods Receipt", "inbound_delivery": ind.name, "warehouse": scenario.warehouse, "receiving_bin": scenario.recv_bin,
            "items": [{"inbound_delivery_item": ind.items[0].name, "item": self.item, "quantity": qty, "stock_uom": self.uom, "handling_unit": hu.name, "stock_type": "AVAILABLE"}]})
        gr.insert(ignore_permissions=True)
        gr.submit()
        task = create_putaway(gr.name)["warehouse_tasks"][0]
        confirm_task(task, confirmed_quantity=qty)
        return hu, task

    def test_putaway_confirmed_queues_a_print_spool(self):
        scenario = self._new_scenario(with_printer_rules=["Putaway Confirmed"])
        _hu, task = self._receive_and_putaway(scenario, 5)
        spool = frappe.get_all("WMS Print Spool", filters={"reference_doctype": "Warehouse Task", "reference_name": task}, fields=["status"])
        self.assertEqual(len(spool), 1)
        self.assertEqual(spool[0].status, "Queued")

    def test_goods_issue_and_shipment_loaded_both_queue_print_spools(self):
        scenario = self._new_scenario(with_printer_rules=["Goods Issue Posted", "Shipment Loaded"])
        self._receive_and_putaway(scenario, 4)
        obd = frappe.get_doc({"doctype": "Outbound Delivery", "outbound_delivery_number": frappe.generate_hash(length=8), "warehouse": scenario.warehouse,
            "customer": self.customer, "delivery_date": nowdate(), "staging_bin": scenario.stage_bin,
            "items": [{"line_number": 1, "item": self.item, "requested_quantity": 4, "stock_uom": self.uom, "required_stock_type": "AVAILABLE"}]})
        obd.insert(ignore_permissions=True)
        obd.submit()
        allocate_delivery(obd.name)
        pick_tasks = create_pick_tasks(obd.name)
        confirm_task(pick_tasks[0], confirmed_quantity=4)
        hu_name = frappe.db.get_value("Warehouse Task", pick_tasks[0], "destination_hu")

        shipment_name = create_shipment(scenario.warehouse, [obd.name])
        confirm_hu_loaded(shipment_name, hu_name)

        shipment_spool = frappe.get_all("WMS Print Spool", filters={"reference_doctype": "WMS Shipment", "reference_name": shipment_name}, fields=["status"])
        self.assertEqual(len(shipment_spool), 1)
        self.assertEqual(shipment_spool[0].status, "Queued")

        gi_name = frappe.get_all("Goods Issue", filters={"outbound_delivery": obd.name}, pluck="name")[0]
        gi_spool = frappe.get_all("WMS Print Spool", filters={"reference_doctype": "Goods Issue", "reference_name": gi_name}, fields=["status"])
        self.assertEqual(len(gi_spool), 1)
        self.assertEqual(gi_spool[0].status, "Queued")
