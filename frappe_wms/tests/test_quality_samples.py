from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_wms.services.quality import complete_inspection, evaluate_samples, generate_samples, record_sample_result
from frappe_wms.services.stock import post_entries


class TestQualitySamplesAndDecisions(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.wh = "WMS-TEST-QIS-WH"
        company = frappe.get_all("Company", limit=1, pluck="name")[0]
        cls.item = frappe.get_all("Item", filters={"is_stock_item": 1}, order_by="creation asc", limit=1, pluck="name")[0]
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        if not frappe.db.exists("WMS Warehouse", cls.wh):
            frappe.get_doc({"doctype": "WMS Warehouse", "warehouse_code": cls.wh, "warehouse_name": cls.wh, "company": company, "default_stock_type": "AVAILABLE", "allow_negative_stock": 1}).insert(ignore_permissions=True)
        cls.st = f"{cls.wh}-ST"
        if not frappe.db.exists("Storage Type", cls.st):
            frappe.get_doc({"doctype": "Storage Type", "warehouse": cls.wh, "storage_type_code": "ST", "storage_type_name": "ST", "storage_role": "Storage", "capacity_check_method": "None", "active": 1, "hu_requirement": "Optional"}).insert(ignore_permissions=True)
        cls.bins = {}
        for code in ("QBIN", "SCRAP"):
            name = cls.bins[code] = f"{cls.wh}-{code}"
            if not frappe.db.exists("Storage Bin", name):
                frappe.get_doc({"doctype": "Storage Bin", "bin_code": name, "warehouse": cls.wh, "storage_type": cls.st, "active": 1, "sequence": 1}).insert(ignore_permissions=True)
        if not frappe.db.exists("Handling Unit Type", "TEST-QIS-PALLET"):
            frappe.get_doc({"doctype": "Handling Unit Type", "hu_type_code": "TEST-QIS-PALLET", "hu_type_name": "QIS Pallet"}).insert(ignore_permissions=True)
        for code, name, category, target, extra in (("TEST-OK", "OK", "Accept", "AVAILABLE", {}), ("TEST-SCRAP", "Scrap", "Reject", "WAREHOUSE_BLOCKED", {"follow_up_action": "Move to Bin", "follow_up_bin": cls.bins["SCRAP"]}),
                ("TEST-REWORK", "Rework", "Other", "PRODUCTION_BLOCKED", {})):
            if not frappe.db.exists("WMS Usage Decision", code):
                frappe.get_doc({"doctype": "WMS Usage Decision", "decision_code": code, "decision_name": name, "category": category, "target_stock_type": target, "active": 1, **extra}).insert(ignore_permissions=True)

    def setUp(self):
        # the ERPNext mirror (a Stock Entry for the stock-type changes) is not under test here: capture it instead of posting
        self.mirrored = []
        patcher = patch("frappe_wms.services.erpnext_sync._insert_and_submit_as_system", side_effect=lambda se: self.mirrored.append([(i.qty, i.to_wms_stock_type) for i in se.items]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hu(self, parent=None):
        return frappe.get_doc({"doctype": "Handling Unit", "hu_number": frappe.generate_hash(length=10), "hu_type": "TEST-QIS-PALLET", "warehouse": self.wh, "current_bin": self.bins["QBIN"], "parent_hu": parent, "status": "Open"}).insert(ignore_permissions=True)

    def _inspection(self, qty, hu=None, **kw):
        hu = hu or self._hu()
        post_entries([{"warehouse": self.wh, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bins["QBIN"], "stock_type": "QUALITY", "stock_uom": self.uom, "quantity": qty, "movement_type": "701"}],
            "Handling Unit", hu.name, f"test-qis:{hu.name}")
        return frappe.get_doc({"doctype": "WMS Quality Inspection", "warehouse": self.wh, "product": self.item, "handling_unit": hu.name, "storage_bin": self.bins["QBIN"], "from_stock_type": "QUALITY", "quantity": qty, "stock_uom": self.uom,
            "passed_to_stock_type": "AVAILABLE", "failed_to_stock_type": "WAREHOUSE_BLOCKED", "status": "Draft", **kw}).insert(ignore_permissions=True)

    def _qty(self, qi, stock_type):
        return frappe.db.get_value("WMS Stock Balance", {"handling_unit": qi.handling_unit, "storage_bin": qi.storage_bin, "stock_type": stock_type, "product": self.item}, "quantity") or 0

    def test_sampling_percentage_of_nested_hus_gives_spread_samples_and_the_samples_decide(self):
        parent = self._hu()
        for _ in range(10): self._hu(parent.name)
        qi = self._inspection(10, hu=parent, sampling_percentage=30, acceptable_failures=0)
        generate_samples(qi)
        qi.reload()
        self.assertEqual(len(qi.samples), 3)
        self.assertEqual(len({s.handling_unit for s in qi.samples}), 3, "three different child HUs")
        with self.assertRaises(frappe.ValidationError): complete_inspection(qi.name)  # results missing
        for s in qi.samples[:2]: record_sample_result(qi.name, s.name, "Pass")
        self.assertEqual(record_sample_result(qi.name, qi.samples[2].name, "Pass"), "Accept")
        complete_inspection(qi.name)
        self.assertEqual((self._qty(qi, "AVAILABLE"), self._qty(qi, "QUALITY")), (10, 0), "all samples passed: the lot is accepted")

    def test_a_failed_sample_beyond_the_acceptable_number_rejects_the_whole_quantity(self):
        qi = self._inspection(20, sampling_percentage=10, acceptable_failures=0)
        generate_samples(qi)
        qi.reload()
        self.assertEqual([(s.quantity_sampled, s.result) for s in qi.samples], [(2, "Pending")], "no nested HUs: a quantity of units")
        self.assertEqual(record_sample_result(qi.name, qi.samples[0].name, "Fail"), "Reject")
        complete_inspection(qi.name)
        self.assertEqual((self._qty(qi, "WAREHOUSE_BLOCKED"), self._qty(qi, "AVAILABLE")), (20, 0))

    def test_usage_decisions_split_the_quantity_move_the_stock_and_raise_follow_up_tasks(self):
        qi = self._inspection(10)
        with self.assertRaises(frappe.ValidationError): complete_inspection(qi.name, decisions=[{"usage_decision": "TEST-OK", "quantity": 6}])  # does not add up
        result = complete_inspection(qi.name, decisions=[{"usage_decision": "TEST-OK", "quantity": 6}, {"usage_decision": "TEST-SCRAP", "quantity": 3}, {"usage_decision": "TEST-REWORK", "quantity": 1}])
        self.assertEqual((result["passed_quantity"], result["failed_quantity"]), (6, 3))
        self.assertEqual([self._qty(qi, t) for t in ("AVAILABLE", "WAREHOUSE_BLOCKED", "PRODUCTION_BLOCKED", "QUALITY")], [6, 3, 1, 0])
        tasks = frappe.get_all("Warehouse Task", filters={"warehouse": self.wh, "destination_bin": self.bins["SCRAP"], "product": self.item}, fields=["planned_quantity", "source_bin"])
        self.assertEqual([(t.planned_quantity, t.source_bin) for t in tasks], [(3, self.bins["QBIN"])])
        self.assertEqual(frappe.db.get_value("WMS Quality Inspection", qi.name, "status"), "Completed")
        self.assertEqual(self.mirrored, [[(6, "AVAILABLE"), (3, "WAREHOUSE_BLOCKED"), (1, "PRODUCTION_BLOCKED")]], "ERPNext sees every stock type change of the decisions")
